# ngx_stream_upstream_check_module  
该模块可以为Nginx提供主动式后端服务器健康检查的功能（检查类型支持 tcp/udp/http）。
1. 整合tcp和http
2. 增加发送字符串检查服务状态
## apply patch
`cd src ; git git apply patch_path`

## build and install

## config examples
```
stream {
    upstream tcp-cluster {
        # simple round-robin
        server 192.168.0.1:3306;
        server 192.168.0.2:3306;
        check interval=3000 rise=2 fall=5 timeout=5000 default_down=true type=tcp;
    }
    upstream udp-cluster {
        # simple round-robin
        server 192.168.0.1:53;
        server 192.168.0.2:53;
        check interval=3000 rise=2 fall=5 timeout=5000 default_down=true type=udp;
    }
    upstream http-cluster {
        # simple round-robin
        server 192.168.0.1:80;
        server 192.168.0.2:80;
        check interval=3000 rise=2 fall=5 timeout=5000 type=http;
        check_keepalive_requests 100;
        check_http_send "HEAD / HTTP/1.1\r\nConnection: keep-alive\r\n\r\n";
        check_http_expect_alive http_2xx http_3xx;
    }
    server {
        listen 3306;
        proxy_pass tcp-cluster;
    }
    server {
        listen 53;
        proxy_pass udp-cluster;
    }
    server {
        listen 80;
        proxy_pass http-cluster;
    }
}
```
