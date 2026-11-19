package com.projectv.agents;

import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import com.projectv.routing.Router;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.HttpServerErrorException;

import java.math.BigDecimal;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.TimeoutException;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.startsWith;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/** No network, no spend: the AICredits client, ledger, guard and router are mocks. */
class AiCreditsFallbackTest {

    private static final UUID VIDEO = UUID.randomUUID();
    private static final List<String> CHAIN = List.of("gemini-3.6-flash", "groq:openai/gpt-oss-120b");
    private static final Exception OUTAGE = new HttpServerErrorException(HttpStatus.SERVICE_UNAVAILABLE, "503 model overloaded");

    private OpenAiCompatChat chat;
    private CostLedger ledger;
    private BudgetGuard budget;
    private Router router;
    private Pricing pricing;

    @BeforeEach
    void setUp() throws Exception {
        chat = mock(OpenAiCompatChat.class);
        ledger = mock(CostLedger.class);
        budget = mock(BudgetGuard.class);
        router = mock(Router.class);
        pricing = new Pricing("../config");
        when(budget.canSpendAicredits(any())).thenReturn(true);
        when(chat.call(anyString(), anyString(), anyString(), eq(String.class)))
                .thenReturn(new OpenAiCompatChat.Result<>("fallback answer", 1_000, 2_000, 3_000));
    }

    private AiCreditsFallback fallback(boolean active) {
        return new AiCreditsFallback(chat, ledger, pricing, budget, router, active);
    }

    @Test
    void a503RetriesTheSameModelOnceOnAicreditsAndWritesTheLedger() {
        String out = fallback(true).retry(VIDEO, "SCRIPT", CHAIN, OUTAGE, "sys", "user", String.class);
        assertEquals("fallback answer", out);
        verify(chat).call(eq("aicredits:google/gemini-3.6-flash"), eq("sys"), eq("user"), eq(String.class));
        // 1,000 in x $1.50/M + 2,000 out x $7.50/M = $0.0165 x 94 x 1.1025
        verify(ledger).record(eq(VIDEO), eq(BudgetGuard.AICREDITS),
                eq(pricing.aicreditsInr("gemini-3.6-flash", 1_000, 2_000)), eq(BigDecimal.valueOf(3_000)), eq("tokens"),
                eq(true), startsWith("fallback: SCRIPT"));
        verify(router).recordExternal(eq(VIDEO), eq("SCRIPT"), eq("llm.text"), eq(BudgetGuard.AICREDITS),
                startsWith("fallback: gemini-3.6-flash failed"), anyList());
    }

    @Test
    void priceIsListTimesFxTimesMarkupFromConfig() {
        assertEquals(0, new BigDecimal("1.71").compareTo(
                pricing.aicreditsInr("gemini-3.6-flash", 1_000, 2_000).setScale(2, java.math.RoundingMode.HALF_UP)));
        assertEquals(0, new BigDecimal("150").compareTo(pricing.aicreditsCapInr()));
        assertEquals("anthropic/claude-sonnet-5.5", pricing.aicreditsModelId("claude-sonnet-5-5"));
    }

    @Test
    void claudeMapsToTheSameClaudeModelNotADowngrade() {
        fallback(true).retry(VIDEO, "SHOT_PLAN", List.of("claude:claude-sonnet-5-5", "gemini-3.6-flash"), OUTAGE,
                "s", "u", String.class);
        verify(chat).call(eq("aicredits:anthropic/claude-sonnet-5.5"), anyString(), anyString(), eq(String.class));
    }

    @Test
    void geminiCapRejectionFallsBack() {
        var cap = new BudgetGuard.BudgetExceededException("Monthly Gemini cap of ₹1000 would be exceeded by SCRIPT");
        assertEquals("fallback answer", fallback(true).retry(VIDEO, "SCRIPT", CHAIN, cap, "s", "u", String.class));
        verify(chat).call(eq("aicredits:google/gemini-3.6-flash"), anyString(), anyString(), eq(String.class));
        verify(ledger).record(eq(VIDEO), eq(BudgetGuard.AICREDITS), any(), any(), eq("tokens"), eq(true),
                startsWith("fallback: SCRIPT"));
    }

    @Test
    void claudeCapRejectionFallsBackToTheSameClaudeModel() {
        var cap = new IllegalStateException("unavailable: Claude monthly cap reached");
        assertEquals("fallback answer", fallback(true).retry(VIDEO, "SHOT_PLAN",
                List.of("claude:claude-sonnet-5-5", "gemini-3.6-flash"), cap, "s", "u", String.class));
        verify(chat).call(eq("aicredits:anthropic/claude-sonnet-5.5"), anyString(), anyString(), eq(String.class));
    }

    @Test
    void capRejectionStopsWhenTheFallbackCapIsUsedUp() {
        when(budget.canSpendAicredits(any())).thenReturn(false);
        var cap = new BudgetGuard.BudgetExceededException("Monthly Gemini cap of ₹1000 would be exceeded by SCRIPT");
        assertNull(fallback(true).retry(VIDEO, "SCRIPT", CHAIN, cap, "s", "u", String.class));
        verifyNoInteractions(chat, ledger, router);
    }

    @Test
    void theAutopilotDailyCapIsNeverWorkedAround() {
        var daily = new BudgetGuard.BudgetExceededException("autopilot daily cap of ₹50 reached");
        assertNull(fallback(true).retry(VIDEO, "SCRIPT", CHAIN, daily, "s", "u", String.class));
        verifyNoInteractions(chat, ledger, router);
    }

    @Test
    void validationAndContentErrorsNeverFallBack() {
        var bad = new HttpClientErrorException(HttpStatus.BAD_REQUEST, "400 invalid argument");
        assertNull(fallback(true).retry(VIDEO, "SCRIPT", CHAIN, bad, "s", "u", String.class));
        assertNull(fallback(true).retry(VIDEO, "SCRIPT", CHAIN,
                new IllegalStateException("blocked: SAFETY"), "s", "u", String.class));
        verifyNoInteractions(chat, ledger, router);
    }

    @Test
    void outagesAreRecognised() {
        assertTrue(AiCreditsFallback.shouldFallBack(OUTAGE));
        assertTrue(AiCreditsFallback.shouldFallBack(new TimeoutException()));
        assertTrue(AiCreditsFallback.shouldFallBack(new RuntimeException("429 RESOURCE_EXHAUSTED: rate limit")));
        assertTrue(AiCreditsFallback.shouldFallBack(new RuntimeException("x", new java.net.ConnectException("refused"))));
        assertFalse(AiCreditsFallback.shouldFallBack(new RuntimeException("404 model not found")));
    }

    @Test
    void offWhenDisabledOrKeyBlank() {
        assertNull(fallback(false).retry(VIDEO, "SCRIPT", CHAIN, OUTAGE, "s", "u", String.class));
        assertFalse(new AiCreditsFallback(ledger, pricing, budget, router, true, "", "http://127.0.0.1:1").active());
        assertFalse(new AiCreditsFallback(ledger, pricing, budget, router, false, "k", "http://127.0.0.1:1").active());
        verifyNoInteractions(chat, ledger, router);
    }

    @Test
    void stopsAtTheFallbackCap() {
        when(budget.canSpendAicredits(any())).thenReturn(false);
        assertNull(fallback(true).retry(VIDEO, "SCRIPT", CHAIN, OUTAGE, "s", "u", String.class));
        verifyNoInteractions(chat, ledger, router);
    }

    @Test
    void noEquivalentModelMeansNoFallback() {
        assertNull(fallback(true).retry(VIDEO, "SCRIPT", List.of("groq:openai/gpt-oss-120b", "gemini-9-unknown"),
                OUTAGE, "s", "u", String.class));
        verify(chat, never()).call(anyString(), anyString(), anyString(), any());
    }
}
