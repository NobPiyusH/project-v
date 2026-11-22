package com.projectv.gpu.broker;

import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

import java.util.regex.Pattern;

/** HTTP helpers shared by the pod providers (RunPod, Vast, E2E): one client recipe, safe env values, short error text. */
final class ProviderHttp {

    /** Characters allowed in a pod env key or value passed through a provider's start script. */
    static final Pattern SAFE_ENV = Pattern.compile("[A-Za-z0-9+/=._:@-]*");
    static final String USER_AGENT = "projectv-studio/1.0";

    private ProviderHttp() {
    }

    /** A RestClient with a 10 s connect timeout, the given read timeout and, when not null, a User-Agent header. */
    static RestClient client(int readTimeoutMillis, String userAgent) {
        SimpleClientHttpRequestFactory f = new SimpleClientHttpRequestFactory();
        f.setConnectTimeout(10_000);
        f.setReadTimeout(readTimeoutMillis);
        RestClient.Builder b = RestClient.builder().requestFactory(f);
        if (userAgent != null) {
            b.defaultHeader("User-Agent", userAgent);
        }
        return b.build();
    }

    /** True when both the key (non-empty) and the value are safe to put in a start script. */
    static boolean safeEnv(String key, String value) {
        return !key.isEmpty() && SAFE_ENV.matcher(key).matches() && SAFE_ENV.matcher(value).matches();
    }

    /** At most 200 characters of a provider's answer, for logs and errors ("" for null). */
    static String abbreviate(String s) {
        return s == null ? "" : s.length() > 200 ? s.substring(0, 200) : s;
    }

    static String str(Object o) {
        return o == null ? "" : o.toString();
    }
}
