package com.projectv.production;

import com.projectv.agents.DirectorAgent;
import com.projectv.agents.WriterAgent;
import com.projectv.assets.AssetStore;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import com.projectv.planner.StylePresets;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.simple.JdbcClient;
import tools.jackson.databind.ObjectMapper;

/** Shared dependencies of the production stages (one bean, so each stage class stays a one-line constructor). */
@org.springframework.stereotype.Component
public class ProductionDeps {
    final JdbcClient jdbc;
    final ObjectMapper json;
    final WriterAgent writer;
    final DirectorAgent director;
    final AssetStore assets;
    final CostLedger ledger;
    final Pricing pricing;
    final BudgetGuard budget;
    final StylePresets presets;
    final String textModel;
    final String ttsModel;
    final String imageModel;
    final String musicModel;
    final String defaultVoice;
    final String modalApp;
    final String modalClass;

    public ProductionDeps(JdbcClient jdbc,
                          ObjectMapper json,
                          WriterAgent writer,
                          DirectorAgent director,
                          AssetStore assets,
                          CostLedger ledger,
                          Pricing pricing,
                          BudgetGuard budget,
                          StylePresets presets,
                          @Value("${projectv.models.text},${projectv.models.text-fallbacks:}") String textModel,
                          @Value("${projectv.models.tts}") String ttsModel,
                          @Value("${projectv.models.image}") String imageModel,
                          @Value("${projectv.models.music}") String musicModel,
                          @Value("${projectv.models.tts-voice}") String defaultVoice,
                          @Value("${projectv.modal.zimage-app}") String modalApp,
                          @Value("${projectv.modal.zimage-class}") String modalClass) {
        this.jdbc = jdbc;
        this.json = json;
        this.writer = writer;
        this.director = director;
        this.assets = assets;
        this.ledger = ledger;
        this.pricing = pricing;
        this.budget = budget;
        this.presets = presets;
        this.textModel = textModel;
        this.ttsModel = ttsModel;
        this.imageModel = imageModel;
        this.musicModel = musicModel;
        this.defaultVoice = defaultVoice;
        this.modalApp = modalApp;
        this.modalClass = modalClass;
    }
}
