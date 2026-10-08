---
title: 给一台没有 IPv6 栈的容器接上 IPv6 出口
date: 2026-10-08
summary: 内核连 AF_INET6 socket 都建不出来的工作区，怎么通过 Tailscale 出口节点 + 用户态网络栈 + 本地分流代理访问 IPv6，同时保持 IPv4 直连。
tags: [tailscale, ipv6, networking, socks5, chromium]
---

> **脱敏说明**：文中地址均为示例值——公网 IPv4 用 RFC 5737 的 `203.0.113.0/24`，
> IPv6 用 RFC 3849 的 `2001:db8::/32`，tailnet 地址写成 `100.x.x.x`。

## 结论先行

- 这台工作区的**内核完全没有 IPv6 协议栈**，所以「给内核加 IPv6 路由」这类方案一律不可行；
- 唯一的出路是**用户态网络栈**：`tailscaled` 用 `--tun=userspace-networking` 自己实现 TCP/IP（含 IPv6），再暴露本地代理；
- 为了让「IPv6 走出口节点、IPv4 直连」，在代理前面加了一层自己写的**按地址族分流**的小代理；
- 浏览器这类不支持命令行代理设置的程序，用 **Chromium 托管策略**接入；
- 登录态放持久盘 + 生命周期钩子，重启后自动恢复、无需重新登录。

---

## 1. 起点：一个「没有 IPv6」的容器

三行命令就能确认：

```bash
$ ls /proc/sys/net/ipv6
ls: cannot access '/proc/sys/net/ipv6': No such file or directory

$ python3 -c 'import socket; socket.socket(socket.AF_INET6, socket.SOCK_STREAM)'
OSError: [Errno 97] Address family not supported by protocol

$ ip -6 route
（空）
```

也就是说：内核没有加载 `ipv6` 模块，没有 `/proc/sys/net/ipv6`，**连创建一个 `AF_INET6` 的 socket 都会直接报错**。

这一点是全篇的前提：**任何「给内核加一条 IPv6 路由」的方案，在这里都是死路**——因为应用层根本建不出 IPv6 的连接。

## 2. 常规做法为什么不行

Tailscale 的常规用法是：`tailscaled` 建一个 `tun0`，把 `100.64.0.0/10` 和出口节点的默认路由写进内核路由表；选一个节点当 exit node，本机流量就从它出去。

但建 tun 有两种模式：

| 模式 | 谁来实现 TCP/IP | 需要内核协议栈吗 |
|---|---|---|
| `--tun=tailscale0`（默认） | **内核** | 是 |
| `--tun=userspace-networking` | tailscaled 自己（Go netstack） | 否 |

默认模式下，IPv6 数据包要交给内核协议栈去处理——而这里的内核没有 IPv6 栈。所以「内核态 tun + exit node」这套在这里直接不成立。

## 3. 可行路径：把 IPv6 放在用户态

换 `--tun=userspace-networking`：`tailscaled` 用 Go 的 netstack 在用户态实现了一整套 TCP/IP（IPv4 和 IPv6 都有），**完全不碰内核路由表**，并对外暴露本地代理：

```bash
sudo tailscaled \
  --state=/data/tunnel/state/tailscaled.state \
  --socket=/var/run/tailscale/tailscaled.sock \
  --tun=userspace-networking \
  --socks5-server=127.0.0.1:1055 \
  --outbound-http-proxy-listen=127.0.0.1:1056
```

选出口节点（用 tailnet IP，这里的 MagicDNS 不太可靠）：

```bash
sudo tailscale up --hostname=memoh-ws --exit-node=100.94.x.x \
  --exit-node-allow-lan-access=false
```

这时 `127.0.0.1:1055` 就是一个**能说 IPv6 的 SOCKS5 代理**：

```bash
$ curl -x socks5h://127.0.0.1:1055 https://v6.ident.me
2001:db8:1::2      # 出口节点 hyhk 看到的 IPv6
```

## 4. 但我不想让 IPv4 也被卷走

直接用这个 SOCKS5 的话，**IPv4 也会从出口节点出去**。而需求是「只给 IPv6 加出口，IPv4 直连」。

普通 SOCKS/HTTP 代理是「全代理」，无法按地址族分流。所以在它前面加一层自己写的分流代理 `v6split`：

```
IPv6 目标 ──► v6split :11080/:11081 ──► 127.0.0.1:1055 ──► hyhk ──► IPv6 互联网
IPv4 目标 ──► 本机默认路由，直连
```

分流规则：

| 目标类型 | 走法 | 出口 |
|---|---|---|
| IPv6 字面量 | 转发给 tailscaled 的 SOCKS5 | 出口节点 |
| IPv4 字面量 | 直接连接 | 本机 |
| 域名（代理侧解析） | 有 A 记录 → 直连 IPv4；只有 AAAA → 走隧道 | 视情况 |

## 5. 两个不做就会踩的细节

### 域名必须交给代理去解析

因为内核没有 IPv6 接口，glibc 的 `getaddrinfo` 会**主动屏蔽 AAAA 结果**——本地解析永远拿不到 IPv6 地址。所以客户端必须让代理来做域名解析（`curl` 用 `socks5h://`，HTTP CONNECT 也一样）。

但反过来，`v6split` 自己判断「这个域名到底有没有 AAAA」时，需要拿到真实结果。可以用**显式地址族**查询：

```python
socket.getaddrinfo(host, port, socket.AF_INET6, socket.SOCK_STREAM)
# 内核没有 v6 也会返回 AAAA —— 这里只是「读」一条记录，并不创建 IPv6 socket
```

### SOCKS5 的域名请求必须带长度前缀

SOCKS5 里 `ATYP=0x03`（域名）的请求格式是 `ATYP | LEN | DOMAIN | PORT`。第一版实现漏掉了 `LEN` 字节，服务端就一直在等「剩下的字节」，表现成莫名其妙的「连接超时」，日志里也看不出原因。补上长度前缀后立刻恢复。

### 顺带一个 shell 坑

`pkill -f <模式>` 会匹配到**执行这条命令的那个 shell 自己**（它的命令行里含有同一个字符串），于是操作到一半就「自杀」了。把这类操作写成脚本文件、再按文件名调用即可避免。

## 6. 让浏览器也用上

工作区里的 Chromium 有两个麻烦：

- 它由桌面（`xfce4-panel`）直接拉起，**没有可以塞启动参数的包装脚本**，不能随手杀掉重启（不会自动拉起）；
- 它的网络服务**不读** GNOME / gsettings 里的系统代理设置。

所以改用 **Chromium 托管策略**：

```json
// /etc/chromium/policies/managed/v6split.json
{
  "ProxySettings": {
    "ProxyMode": "fixed_servers",
    "ProxyServer": "socks5://127.0.0.1:11080",
    "ProxyBypassList": "localhost,127.0.0.1,[::1],100.64.0.0/10"
  }
}
```

Chromium 会**监视这个目录并热加载**——写完立刻生效，**不用重启浏览器**。`ProxyBypassList` 把 `localhost` 放回直连，避免影响工作区里的本地预览链接。

验证：浏览器打开 `https://v6.ident.me` 返回出口节点的 IPv6；打开 IPv4 测速站则仍是本机直连地址，两边同时可见。

## 7. 重启之后自动恢复

容器的 `/` 是 overlay，不保证留存；`/data` 是独立持久盘。所以：

- `tailscaled` 的登录态（节点密钥）放到 `/data/tunnel/state/` ——**重启后无需重新登录**；
- 用 Memoh 钩子的 `WorkspaceStart` / `SessionStart` 事件触发一个入口脚本，入口脚本把 `up.sh` 后台分离执行（钩子有 10s 超时）；
- `up.sh` 本身幂等：进程还在就跳过，不在就拉起。

做了**真实的恢复演练**（不是假设）：把相关进程全部杀掉、删掉 `tun0`、清空路由——模拟重启后的空容器——再触发入口脚本，**约 9 秒**恢复到可用状态，并且没有要求重新登录。

## 8. 验证

| 检查 | 期望 | 结果 |
|---|---|---|
| `ip route` | 只有 `default` + 链路路由 | ✅ IPv4 未被改动 |
| 直连 IPv4 | 本机地址 | ✅ `203.0.113.10` |
| 经代理访问纯 IPv6 站 | 出口节点 IPv6 | ✅ `2001:db8:1::2` |
| 经代理访问纯 IPv4 站 | 本机地址 | ✅ `203.0.113.10` |
| 经代理访问双栈站 | 直连 IPv4（默认偏好） | ✅ |
| 浏览器打开纯 IPv6 站 | 出口节点 IPv6 | ✅ |
| `tailscale status` | `Running` | ✅ |

## 9. 局限

- **IPv6 只对「会走代理」的程序生效**。内核没有 v6 栈是硬约束，`ping6`、裸 socket、多数非 HTTP 协议仍然没有 IPv6。
- 出口链路走的是 **DERP 中继**（本机 UDP 出网被封，打不了洞），实测延迟约 79ms、吞吐约 2MB/s。所以双栈站点默认走直连 IPv4 更划算。
- 想让双栈站点也优先走 IPv6，可以用环境变量 `V6SPLIT_PREFER_V6=1` 重启分流代理来反转偏好。
- 整套东西都依赖工作区里的免密 `sudo`；且不改动平台托管的配置（例如 `/etc/resolv.conf`）。

## 附：涉及的文件

| 文件 | 作用 |
|---|---|
| `tailscaled.sh` | 启动守护进程（userspace 模式，登录态在 `/data`） |
| `v6split.py` | 分流代理（SOCKS5 `:11080`、HTTP CONNECT `:11081`） |
| `v6split.sh` | 后台启动分流代理（幂等） |
| `keepalive.sh` | 自愈循环：进程掉了就拉起来 |
| `up.sh` | 幂等全量拉起 |
| `autostart.sh` | 钩子入口，后台分离后立刻返回 |
| `browser-proxy.sh` | 给 Chromium 写托管策略 |
| `env.sh` / `v6.sh` | 让命令行工具走分流代理 |