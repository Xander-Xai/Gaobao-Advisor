package com.xsuperplay.gaobao.gateway;

import java.time.Duration;
import java.util.List;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.cloud.gateway.filter.GatewayFilter;
import org.springframework.cloud.gateway.filter.factory.AbstractGatewayFilterFactory;
import org.springframework.data.redis.core.ReactiveStringRedisTemplate;
import org.springframework.data.redis.core.script.RedisScript;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

import reactor.core.publisher.Mono;

/**
 * Atomic Redis-backed, globally shared public catalog token bucket.
 *
 * Spring Cloud Gateway's built-in RedisRateLimiter may admit requests after
 * a Redis error, so the public catalog uses an explicit fail-closed filter.
 * The Redis server's TIME avoids gateway clock skew; one Lua script performs
 * the refill and debit atomically across any number of gateway replicas.
 */
@Component
public final class FailClosedCatalogRateLimiterGatewayFilterFactory
        extends AbstractGatewayFilterFactory<FailClosedCatalogRateLimiterGatewayFilterFactory.Config> {

    private static final String BUCKET_KEY = "gaobao:catalog:public:v2";
    private static final String LUA = """
            local key = KEYS[1]
            local rate = tonumber(ARGV[1])
            local capacity = tonumber(ARGV[2])
            local requested = 1
            local t = redis.call('TIME')
            local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
            local previous = redis.call('HMGET', key, 'tokens', 'updated')
            local tokens = tonumber(previous[1]) or capacity
            local updated = tonumber(previous[2]) or now
            tokens = math.min(capacity, tokens + math.max(0, now - updated) * rate)
            local allowed = 0
            if tokens >= requested then
                tokens = tokens - requested
                allowed = 1
            end
            redis.call('HSET', key, 'tokens', tokens, 'updated', now)
            redis.call('PEXPIRE', key, math.max(1000, math.ceil(capacity / rate * 2000)))
            return allowed
            """;
    private static final RedisScript<Long> SCRIPT = RedisScript.of(LUA, Long.class);

    private final ReactiveStringRedisTemplate redis;
    private final int rate;
    private final int capacity;

    public FailClosedCatalogRateLimiterGatewayFilterFactory(
            ReactiveStringRedisTemplate redis,
            @Value("${CATALOG_RATE_PER_SECOND:20}") int rate,
            @Value("${CATALOG_RATE_BURST:40}") int capacity) {
        super(Config.class);
        if (rate <= 0 || capacity <= 0) {
            throw new IllegalArgumentException("Catalog rate and burst must be positive");
        }
        this.redis = redis;
        this.rate = rate;
        this.capacity = capacity;
    }

    public static final class Config {
    }

    @Override
    public GatewayFilter apply(Config config) {
        return (exchange, chain) -> redis
                .execute(
                        SCRIPT,
                        List.of(BUCKET_KEY),
                        List.of(Integer.toString(rate), Integer.toString(capacity)))
                .next()
                .switchIfEmpty(Mono.error(new IllegalStateException("Redis limiter returned no decision")))
                .timeout(Duration.ofSeconds(3))
                .flatMap(allowed -> {
                    if (allowed == 1L) {
                        return chain.filter(exchange);
                    }
                    exchange.getResponse().setStatusCode(HttpStatus.TOO_MANY_REQUESTS);
                    return exchange.getResponse().setComplete();
                })
                // Any Redis outage / timeout / script problem fails CLOSED (503).
                .onErrorResume(error -> {
                    if (!exchange.getResponse().isCommitted()) {
                        exchange.getResponse().setStatusCode(HttpStatus.SERVICE_UNAVAILABLE);
                        return exchange.getResponse().setComplete();
                    }
                    return Mono.error(error);
                });
    }
}
