// frontend/src/js/config.js

/**
 * Runtime-independent конфигурация frontend.
 */

export const ANALYSES_ENDPOINT = "/api/v1/analyses";

export const ANALYSIS_POLL_INTERVAL_MS = 2000;

export const ANALYSIS_MAX_POLL_ATTEMPTS = 1800;

/**
 * Gateway уже принимает поля ПЗ.
 *
 * В UI функция будет включена после того, как PZ-2/PZ-3
 * начнут реально учитывать этот контекст в новой V2-цепочке.
 */
export const EXPLANATORY_NOTE_ENABLED = false;