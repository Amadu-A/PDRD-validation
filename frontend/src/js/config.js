// frontend/src/js/config.js

/**
 * Runtime-independent конфигурация frontend.
 */

export const ANALYSES_ENDPOINT = "/api/v1/analyses";

export const ANALYSIS_POLL_INTERVAL_MS = 2000;

export const ANALYSIS_MAX_POLL_ATTEMPTS = 1800;

/**
 * Контекст ПЗ полностью проходит через Gateway -> V2 pipeline.
 */
export const EXPLANATORY_NOTE_ENABLED = true;