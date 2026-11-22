package com.projectv.agents;

import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.beans.factory.ObjectProvider;

import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;

/** projectv.cost.auto-retry-timeout: one retry after a timeout when on; exactly the old behaviour (fail, wait for the owner) when off. */
class LlmTimeoutRetryTest {

    record Out(String v) {
    }

    @SuppressWarnings("unchecked")
    private Llm llm(OpenAiCompatChat nvidia, boolean retry) {
        ObjectProvider<ChatModel> provider = Mockito.mock(ObjectProvider.class);
        Mockito.when(provider.getIfAvailable()).thenReturn(Mockito.mock(ChatModel.class));
        Llm llm = new Llm(provider, Mockito.mock(CostLedger.class), Mockito.mock(Pricing.class), Mockito.mock(BudgetGuard.class),
                nvidia, Mockito.mock(ClaudeChat.class), Mockito.mock(AiCreditsFallback.class), 1, "nvidia:a", "");
        llm.setRetryTimeout(retry);
        return llm;
    }

    private OpenAiCompatChat slowThenFast(AtomicInteger calls) {
        OpenAiCompatChat n = Mockito.mock(OpenAiCompatChat.class);
        Mockito.when(n.call(anyString(), anyString(), anyString(), eq(Out.class))).thenAnswer(i -> {
            if (calls.incrementAndGet() == 1) {
                Thread.sleep(2500); // longer than the 1 s limit
            }
            return new OpenAiCompatChat.Result<>(new Out("ok"), 1, 1, 2);
        });
        return n;
    }

    @Test
    void onTimeoutTheCallIsRetriedOnceWhenTheFlagIsOn() {
        AtomicInteger calls = new AtomicInteger();
        Out o = llm(slowThenFast(calls), true).call(UUID.randomUUID(), "SHOT_PLAN", "s", "u", Out.class);
        assertEquals("ok", o.v());
        assertEquals(2, calls.get());
    }

    @Test
    void offMeansTheStageFailsAsBefore() {
        AtomicInteger calls = new AtomicInteger();
        var ex = assertThrows(IllegalStateException.class, () -> llm(slowThenFast(calls), false).call(UUID.randomUUID(), "SHOT_PLAN", "s", "u", Out.class));
        assertEquals(true, ex.getMessage().contains("answered within"));
        assertEquals(1, calls.get());
    }

    @Test
    void aSecondTimeoutIsNotRetriedAgain() {
        AtomicInteger calls = new AtomicInteger();
        OpenAiCompatChat n = Mockito.mock(OpenAiCompatChat.class);
        Mockito.when(n.call(anyString(), anyString(), anyString(), eq(Out.class))).thenAnswer(i -> {
            calls.incrementAndGet();
            Thread.sleep(2500);
            return null;
        });
        assertThrows(IllegalStateException.class, () -> llm(n, true).call(UUID.randomUUID(), "SHOT_PLAN", "s", "u", Out.class));
        assertEquals(2, calls.get(), "one retry, then it fails for the owner");
    }
}
