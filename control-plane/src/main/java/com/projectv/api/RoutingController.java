package com.projectv.api;

import com.projectv.routing.Router;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;
import java.util.UUID;

/** M3a: what the router decided and why, and which providers are cooling down (the M3b dashboard reads these). */
@RestController
@RequestMapping("/api")
public class RoutingController {

    private final Router router;
    private final JdbcClient jdbc;

    public RoutingController(Router router, JdbcClient jdbc) {
        this.router = router;
        this.jdbc = jdbc;
    }

    @GetMapping("/videos/{id}/routing")
    public List<Map<String, Object>> decisions(@PathVariable UUID id) {
        return router.decisions(id);
    }

    @GetMapping("/routing/health")
    public List<Map<String, Object>> health() {
        return jdbc.sql("SELECT provider_id, fails, cooldown_until, last_error, updated_at FROM provider_health ORDER BY provider_id")
                .query().listOfRows();
    }
}
