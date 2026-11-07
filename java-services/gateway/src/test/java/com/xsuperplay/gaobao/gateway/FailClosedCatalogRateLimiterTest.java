package com.xsuperplay.gaobao.gateway;

import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

import org.junit.jupiter.api.Test;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.data.redis.core.ReactiveStringRedisTemplate;
import org.springframework.data.redis.core.script.RedisScript;
import org.springframework.http.HttpStatus;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;

import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class FailClosedCatalogRateLimiterTest {
    @SuppressWarnings({"unchecked", "rawtypes"})
    private static ReactiveStringRedisTemplate mockRedis(Flux<Long> result) {
        var redis = mock(ReactiveStringRedisTemplate.class);
        when(redis.execute(any(RedisScript.class), anyList(), anyList())).thenReturn((Flux) result);
        return redis;
    }

    private static MockServerWebExchange exchange() {
        return MockServerWebExchange.from(
                MockServerHttpRequest.get("/api/v1/catalog/schools?limit=1"));
    }

    private static GatewayFilterChain chain(AtomicBoolean called) {
        return serverWebExchange -> {
            called.set(true);
            return Mono.empty();
        };
    }

    @Test
    void successfulRedisAtomicDecisionForwards() {
        var called = new AtomicBoolean();
        var factory = new FailClosedCatalogRateLimiterGatewayFilterFactory(
                mockRedis(Flux.just(1L)), 20, 40);
        var exchange = exchange();
        factory.apply(new FailClosedCatalogRateLimiterGatewayFilterFactory.Config())
                .filter(exchange, chain(called)).block();
        assertTrue(called.get());
        assertNull(exchange.getResponse().getStatusCode());
    }

    @Test
    void emptyBucketRejectsWith429() {
        var called = new AtomicBoolean();
        var factory = new FailClosedCatalogRateLimiterGatewayFilterFactory(
                mockRedis(Flux.just(0L)), 1, 4);
        var exchange = exchange();
        factory.apply(new FailClosedCatalogRateLimiterGatewayFilterFactory.Config())
                .filter(exchange, chain(called)).block();
        assertFalse(called.get());
        assertEquals(HttpStatus.TOO_MANY_REQUESTS, exchange.getResponse().getStatusCode());
    }

    @Test
    void redisFailureRejectsWith503RatherThanAdmitting() {
        var called = new AtomicBoolean();
        var factory = new FailClosedCatalogRateLimiterGatewayFilterFactory(
                mockRedis(Flux.error(new IllegalStateException("redis unavailable"))), 1, 4);
        var exchange = exchange();
        factory.apply(new FailClosedCatalogRateLimiterGatewayFilterFactory.Config())
                .filter(exchange, chain(called)).block();
        assertFalse(called.get());
        assertEquals(HttpStatus.SERVICE_UNAVAILABLE, exchange.getResponse().getStatusCode());
    }

    @Test
    void emptyRedisDecisionAlsoFailsClosed() {
        var called = new AtomicBoolean();
        var factory = new FailClosedCatalogRateLimiterGatewayFilterFactory(
                mockRedis(Flux.empty()), 1, 4);
        var exchange = exchange();
        factory.apply(new FailClosedCatalogRateLimiterGatewayFilterFactory.Config())
                .filter(exchange, chain(called)).block();
        assertFalse(called.get());
        assertEquals(HttpStatus.SERVICE_UNAVAILABLE, exchange.getResponse().getStatusCode());
    }


    @Test
    @SuppressWarnings({"unchecked", "rawtypes"})
    void allReplicasUseTheSameBucketDespiteSpoofedForwardedAddresses() {
        var redis = mockRedis(Flux.just(1L));
        var factory = new FailClosedCatalogRateLimiterGatewayFilterFactory(redis, 20, 40);
        var filter = factory.apply(new FailClosedCatalogRateLimiterGatewayFilterFactory.Config());
        for (String ip : List.of("1.2.3.4", "8.8.8.8")) {
            var exchange = MockServerWebExchange.from(
                    MockServerHttpRequest.get("/api/v1/catalog/schools")
                            .header("X-Forwarded-For", ip));
            filter.filter(exchange, chain(new AtomicBoolean())).block();
        }
        verify(redis, times(2)).execute(
                any(RedisScript.class), eq(List.of("gaobao:catalog:public:v2")), anyList());
    }

    @Test
    void invalidLimiterConfigurationIsRejectedAtStartup() {
        assertThrows(IllegalArgumentException.class,
                () -> new FailClosedCatalogRateLimiterGatewayFilterFactory(mockRedis(Flux.just(1L)), 0, 4));
    }
}
