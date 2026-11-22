/*
 * Copyright (C) 2014 Weibin Yao (yaoweibin@gmail.com)
 * Copyright (C) 2010-2014 Alibaba Group Holding Limited
 * Copyright (C) 2017 Changxun Zhou (zhoucx@dtdream.com)
 * Copyright (C) 2017-2026 Ziyao Chen
 */


#ifndef _NGX_STREAM_UPSTREAM_CHECK_MODULE_H_INCLUDED_
#define _NGX_STREAM_UPSTREAM_CHECK_MODULE_H_INCLUDED_


#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_stream.h>


/*
 * Register a backend server with the health checker. Returns its check id
 * (> 0), 0 if the upstream has no "check" directive, or (ngx_uint_t) NGX_ERROR
 * on allocation failure. Peers that never call this keep check id 0.
 */
ngx_uint_t ngx_stream_upstream_check_add_peer(ngx_conf_t *cf,
    ngx_stream_upstream_srv_conf_t *us, ngx_addr_t *peer);

/* 1 if the checker currently marks the peer down; 0 for check id 0 */
ngx_uint_t ngx_stream_upstream_check_peer_down(ngx_uint_t id);

/* busyness counters, not used by the core patch */
void ngx_stream_upstream_check_get_peer(ngx_uint_t id);
void ngx_stream_upstream_check_free_peer(ngx_uint_t id);


#endif /* _NGX_STREAM_UPSTREAM_CHECK_MODULE_H_INCLUDED_ */
