package com.xsuperplay.gaobao.gateway;

import org.springframework.cloud.gateway.filter.ratelimit.KeyResolver;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import reactor.core.publisher.Mono;

/**
 * One globally shared public-catalog bucket across every gateway replica.
 * Deliberately does not trust client-controlled X-Forwarded-For or IP headers.
 * Do not apply this anonymous global bucket to user sessions or the chat SSE route.
 */
@Configuration
public class RateLimitKeys {
    @Bean
    KeyResolver catalogGlobalKeyResolver() {
        return exchange -> Mono.just("gaobao:catalog:public");
    }
}
