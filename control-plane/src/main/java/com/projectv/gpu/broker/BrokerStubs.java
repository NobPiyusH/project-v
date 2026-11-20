package com.projectv.gpu.broker;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** Thin placeholders for the later adapters (owner: Jarvislabs, CloudRift next; Vast.ai, Shadeform, Modal later). */
@Configuration
class BrokerStubs {

    @Bean
    GpuProvider jarvislabsProvider() {
        return new StubProvider("jarvislabs", "wrap the `jl` CLI / JL_API_KEY; A100 40GB Rs84/h seen");
    }

    @Bean
    GpuProvider cloudriftProvider() {
        return new StubProvider("cloudrift", "REST https://api.cloudrift.ai, X-API-Key, {version,data} bodies; key not in .env yet");
    }

    @Bean
    GpuProvider vastProvider() {
        return new StubProvider("vast", "no key yet");
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
