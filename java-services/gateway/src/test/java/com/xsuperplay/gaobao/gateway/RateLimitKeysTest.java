package com.xsuperplay.gaobao.gateway;

import org.junit.jupiter.api.Test;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;

import static org.junit.jupiter.api.Assertions.assertEquals;

class RateLimitKeysTest {
    @Test
    void allGatewayReplicasUseTheSameBucketEvenIfIpHeadersDiffer() {
        var resolver = new RateLimitKeys().catalogGlobalKeyResolver();
        var a = MockServerWebExchange.from(MockServerHttpRequest.get("/api/v1/catalog/schools")
                .header("X-Forwarded-For", "1.2.3.4"));
        var b = MockServerWebExchange.from(MockServerHttpRequest.get("/api/v1/catalog/schools")
                .header("X-Forwarded-For", "8.8.8.8"));
        assertEquals("gaobao:catalog:public", resolver.resolve(a).block());
        assertEquals(resolver.resolve(a).block(), resolver.resolve(b).block());
    }
}
