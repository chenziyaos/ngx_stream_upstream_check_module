# ngx_stream_upstream_check_module

给 nginx `stream` 模块（四层 TCP/UDP 代理）加主动健康检查：按固定间隔探测 upstream 里的每个 server，连续失败 `fall` 次标记为 down，负载均衡时跳过；连续成功 `rise` 次恢复。另带一个 HTTP 状态页，可输出 HTML、CSV、JSON。

nginx 开源版的 stream upstream 只有被动检查（`max_fails` / `fail_timeout`），主动检查 `health_check` 只在 NGINX Plus 里有。

| 检查方式 | 配置 | 判定为 up 的条件 |
|---|---|---|
| TCP 连接 | `type=tcp` | 能建立连接 |
| TCP 发送并匹配 | `type=tcp` + `check_backend_expect_alive`（可选 `check_send`） | 发出 `check_send` 的内容（可以不发，只读 banner）后，回包里包含期望的字符串（不区分大小写） |
| UDP | `type=udp` | 发出一个数据报后没有收到 ICMP 端口不可达 |
| UDP 发送并匹配 | `type=udp` + `check_send` + `check_backend_expect_alive` | 在 `timeout` 内收到回包，且回包包含期望的字符串 |
| HTTP | `type=http` + `check_http_send` + `check_http_expect_alive` | 状态码落在期望的区间 |

## 兼容性

| nginx 版本 | 核心补丁 | 说明 |
|---|---|---|
| 1.31.x（mainline） | `patch/nginx-1.31.patch` | 覆盖 round robin、hash、least_conn、random、least_time |
| 1.28 – 1.30（stable 1.30.x） | `patch/nginx-1.28-1.30.patch` | 覆盖 round robin、hash、least_conn、random |
| 1.24 – 1.26 | `patch/nginx-1.24-1.26.patch` | 同上。1.27.x 没有对应补丁 |
| 1.12 – 1.22 | `patch/legacy/nginx-1.12-1.22.patch` | 2017 年的原始补丁，不覆盖 random：用 `random` 时流量仍会打到 down 的节点 |

CI 在 Ubuntu 24.04 上对 1.22.1、1.24.0、1.26.3、1.28.3、1.30.7、1.31.9 逐个编译并跑集成测试。没有列出的版本没有验证过。补丁要按上表的版本选：GNU patch 对不上时会报错，但 macOS 自带的 patch 和 `git apply` 可能把补丁打到别的位置而不报错（1.30 的补丁打到 1.24 上就是这样）。

## 编译

模块要改 nginx 核心（在 upstream peer 结构里加一个字段，并在各负载均衡算法里跳过 down 的节点），所以只能静态编译，不能做成动态模块。

一键脚本会下载 nginx 源码、按版本打补丁、编译：

```bash
./build.sh 1.30.7                      # 后面可以接任意 ./configure 参数
./build.sh 1.31.9 --with-debug --with-stream_ssl_module
# 产物：build/nginx-<版本>/objs/nginx
```

手工编译：

```bash
cd nginx-1.30.7
patch -p1 < /path/to/ngx_stream_upstream_check_module/patch/nginx-1.28-1.30.patch
./configure --with-stream --add-module=/path/to/ngx_stream_upstream_check_module
make && make install
```

依赖和 nginx 本身一样：PCRE2（或 PCRE）、zlib、OpenSSL（启用 SSL 时）。macOS 上用 Homebrew 装的库时，加上
`--with-cc-opt="-I$(brew --prefix)/include" --with-ld-opt="-L$(brew --prefix)/lib"`。

## 配置示例

```nginx
stream {
    upstream mysql {
        server 10.0.0.1:3306;
        server 10.0.0.2:3306;
        check interval=3000 rise=2 fall=3 timeout=2000 type=tcp;
    }

    upstream redis {
        server 10.0.0.11:6379;
        server 10.0.0.12:6379;
        check interval=2000 rise=2 fall=3 timeout=1000 type=tcp;
        check_send "PING\r\n";
        check_backend_expect_alive "+PONG";
    }

    upstream smtp {
        server 10.0.0.21:25;
        check interval=5000 rise=2 fall=3 timeout=2000 type=tcp;
        check_backend_expect_alive "220 ";      # 只读服务端的欢迎行，不发送
    }

    upstream dns {
        server 10.0.0.31:53;
        server 10.0.0.32:53;
        check interval=3000 rise=2 fall=3 timeout=1000 type=udp;
    }

    upstream web {
        server 10.0.0.41:80;
        server 10.0.0.42:80;
        check interval=3000 rise=2 fall=5 timeout=2000 type=http;
        check_keepalive_requests 100;
        check_http_send "HEAD /health HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n";
        check_http_expect_alive http_2xx http_3xx;
    }

    server { listen 3306; proxy_pass mysql; }
    server { listen 6379; proxy_pass redis; }
    server { listen 25;   proxy_pass smtp; }
    server { listen 53 udp; proxy_pass dns; }
    server { listen 8080; proxy_pass web; }
}

http {
    server {
        listen 127.0.0.1:8081;
        location /l4status {
            l4check_status;
            allow 127.0.0.1;
            deny all;
        }
    }
}
```

## 指令

### check

```
check interval=毫秒 rise=次数 fall=次数 timeout=毫秒 default_down=true|false type=tcp|udp|http [port=端口];
```

上下文 `upstream`。不写某个参数时取默认值：

| 参数 | 默认值 | 含义 |
|---|---|---|
| `interval` | 30000 | 两次检查的间隔（毫秒） |
| `rise` | 2 | 连续成功多少次后标记为 up |
| `fall` | 5 | 连续失败多少次后标记为 down |
| `timeout` | 1000 | 单次检查的超时（毫秒） |
| `default_down` | true | 启动时（第一次检查完成前）是否当作 down |
| `type` | tcp | 检查方式，见上文表格 |
| `port` | server 自己的端口 | 检查改用另一个端口，例如业务端口和健康检查端口分开时 |

`default_down=true` 意味着 nginx 刚启动或 reload 后，在第一轮检查通过之前所有节点都是 down，这段时间的连接会因为没有可用节点而失败；对此敏感时用 `default_down=false`。

### check_send / check_http_send

```
check_send "内容";
```

上下文 `upstream`。每次检查时发出的内容，支持 `\r\n` 这类转义。`check_http_send` 是同一个指令的旧名字，保留兼容。

- `type=http` 默认发送 `GET / HTTP/1.0\r\n\r\n`。
- `type=udp` 默认发送一个字节 `X`。
- `type=tcp` 只有配了 `check_backend_expect_alive` 才会发送，否则是纯连接检查；只配 `check_send` 不配期望值时 `nginx -t` 报错。

### check_backend_expect_alive

```
check_backend_expect_alive "字符串";
```

上下文 `upstream`，只用于 `type=tcp` 和 `type=udp`。回包里包含这个字符串（不区分大小写）就算成功。

- TCP：一直读到出现匹配、对端关闭或超时；读满 16 KB 仍未匹配算失败。
- UDP：只看收到的第一个数据报；超时没有回包算失败。
- `type=http` 用 `check_http_expect_alive`，在 http 上配这个指令 `nginx -t` 报错。

### check_http_expect_alive

```
check_http_expect_alive http_2xx | http_3xx | http_4xx | http_5xx ...;
```

上下文 `upstream`，默认 `http_2xx http_3xx`。

### check_keepalive_requests

```
check_keepalive_requests 次数;
```

上下文 `upstream`，默认 1。一条检查连接上最多发多少次请求，大于 1 时要在 `check_http_send` 里带 `Connection: keep-alive`。

### check_shm_size

```
check_shm_size 大小;
```

上下文 `stream`。存放检查状态的共享内存大小，默认也是最小值 1M（更小的值会被忽略），后端数量很多时调大。

### l4check_status

```
l4check_status [html|csv|json];
```

上下文 http 的 `server` 或 `location`。输出所有被检查节点的状态，默认 html。也可以用请求参数选择：

- `?format=html|csv|json`
- `?status=up` 或 `?status=down` 只列某一种状态

```bash
curl -s 'http://127.0.0.1:8081/l4status?format=json&status=down'
```

输出形如（数值为示意）：

```json
{"servers": {
  "total": 1,
  "generation": 1,
  "server": [
    {"index": 1, "upstream": "redis", "name": "10.0.0.12:6379", "status": "down", "rise": 0, "fall": 7, "type": "tcp", "port": 0}
  ]
}}
```

`rise` / `fall` 是当前连续成功 / 失败的次数，`port` 为 0 表示用 server 自己的端口，`total` 是本次输出的条数。

## 限制

- 只检查配置文件里静态写出的 server。带 `resolve` 参数、通过 DNS 动态解析出来的节点、`proxy_pass` 直接写地址形成的隐式 upstream 都不检查，照常参与负载均衡。
- 标了 `down` 的 server 本来就不接流量，也不检查。
- 只能静态编译。打过补丁的 nginx 和官方二进制的 `--with-compat` 动态模块不兼容，第三方动态模块要用这份源码重新编译。
- 不带期望值的 UDP 检查只能发现 ICMP 端口不可达。对端主机整个不通、或者中间有防火墙丢弃 ICMP 时，检查会一直认为是 up；要可靠地检查 UDP 服务，配 `check_send` + `check_backend_expect_alive`。
- 一个 upstream 里所有节点都 down 时，nginx 记 `no live upstreams` 并断开新连接，不会退回到"全部放行"。
- 同一个节点的检查同一时刻只由一个 worker 执行（靠共享内存里的 owner 字段协调），检查结果放在共享内存里，所有 worker 看到的是同一份。

## 测试

```bash
./build.sh 1.30.7
python3 tests/run_tests.py build/nginx-1.30.7/objs/nginx
```

测试脚本只用 Python 3 标准库，会在 `127.0.0.1` 上起一组临时的 TCP、UDP、HTTP 后端和一个 nginx，覆盖上面每种检查方式、每种负载均衡算法是否跳过 down 的节点、状态页的三种格式和过滤、以及两条 `nginx -t` 配置错误。跑完自动清理；设置 `KEEP=1` 会保留临时目录（里面有 nginx.conf 和 error.log）。

## 来源和许可

- 检查框架来自 Weibin Yao 的 [nginx_upstream_check_module](https://github.com/yaoweibin/nginx_upstream_check_module)（HTTP upstream 版，Tengine 也内置了它）。
- Changxun Zhou 在 2017 年把它移植到 stream 模块，发布为 [ngx_stream_upstream_check_module](https://github.com/zhouchangxun/ngx_stream_upstream_check_module)，后来演进成 [ngx_healthcheck_module](https://github.com/zhouchangxun/ngx_healthcheck_module)（同时支持 http 和 stream）。
- 本仓库在此基础上加了发送并匹配的检查，并适配 nginx 1.24 – 1.31。

许可证是 BSD 2-Clause，见 [LICENSE](LICENSE)，保留了上游各位作者的版权声明。
