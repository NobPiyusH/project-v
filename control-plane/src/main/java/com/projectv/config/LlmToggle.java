package com.projectv.config;

import org.springframework.boot.EnvironmentPostProcessor;
import org.springframework.boot.SpringApplication;
import org.springframework.core.Ordered;
import org.springframework.core.env.ConfigurableEnvironment;
import org.springframework.core.env.MapPropertySource;

import java.util.Map;

/**
 * Spring AI's Gemini auto-configuration refuses to start without a key. When GEMINI_API_KEY is empty we switch the
 * chat model off, so the app still starts and the Planner uses its rule-based fallback. Runs after .env is loaded.
 */
public class LlmToggle implements EnvironmentPostProcessor, Ordered {

    @Override
    public void postProcessEnvironment(ConfigurableEnvironment env, SpringApplication application) {
        String key = env.getProperty("GEMINI_API_KEY", "");
        if (key.isBlank() && !env.containsProperty("spring.ai.model.chat")) {
            env.getPropertySources().addFirst(new MapPropertySource("projectv-llm-toggle",
                    Map.of("spring.ai.model.chat", "none")));
        }
    }

    @Override
    public int getOrder() {
        return Ordered.LOWEST_PRECEDENCE;
    }
}
