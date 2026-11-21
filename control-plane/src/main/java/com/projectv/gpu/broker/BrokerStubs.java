package com.projectv.gpu.broker;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** Thin placeholders for the later adapters (owner: CloudRift next; Jarvislabs is built, see JarvislabsProvider; Shadeform, Modal later; Vast.ai is built, see VastProvider). */
@Configuration
class BrokerStubs {

    @Bean
    GpuProvider cloudriftProvider() {
        return new StubProvider("cloudrift", "REST https://api.cloudrift.ai, X-API-Key, {version,data} bodies; key not in .env yet");
    }

    @Bean
    GpuProvider shadeformProvider() {
        return new StubProvider("shadeform", "no key yet");
    }

    @Bean
    GpuProvider modalProvider() {
        return new StubProvider("modal", "last fallback from 1 Dec 2026; today Modal runs through the existing media activities");
    }
}
