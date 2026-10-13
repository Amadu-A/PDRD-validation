// frontend/src/js/features/help/page.js

/** Справка открывается гостю; сессия и API для чтения руководства не нужны. */
import { mountHelpCenter } from "./controller.js";

mountHelpCenter(document.querySelector("[data-help-center]"));
