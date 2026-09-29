document.addEventListener("DOMContentLoaded", () => {
  const CONSENT_COOKIE_NAME = "palin_consent";
  const GA4_CLIENT_ID_RE = /^\d{1,21}\.\d{1,21}$/;
  const GA4_SESSION_ID_RE = /^\d{1,21}$/;
  const YANDEX_CLIENT_ID_RE = /^\d{1,32}$/;

  function readCookieValue(name) {
    const parts = (document.cookie || "").split(";").map((cookiePart) => cookiePart.trim());
    for (const part of parts) {
      if (part.startsWith(`${name}=`)) {
        return decodeURIComponent(part.slice(`${name}=`.length));
      }
    }
    return "";
  }

  function hasAnalyticsConsent() {
    const raw = readCookieValue(CONSENT_COOKIE_NAME);
    if (!raw) {
      return false;
    }
    try {
      const parsed = JSON.parse(raw);
      return parsed && parsed.a === true;
    } catch {
      return false;
    }
  }

  function readYandexClientId() {
    const cleaned = String(readCookieValue("_ym_uid") || "").trim();
    return YANDEX_CLIENT_ID_RE.test(cleaned) ? cleaned : "";
  }

  function normalizeGa4ClientId(value) {
    const cleaned = String(value || "").trim();
    return GA4_CLIENT_ID_RE.test(cleaned) ? cleaned : "";
  }

  function normalizeGa4SessionId(value) {
    const cleaned = String(value || "").trim();
    return GA4_SESSION_ID_RE.test(cleaned) ? cleaned : "";
  }

  function parseGaClientIdFromGaCookie(raw) {
    const cleaned = String(raw || "").trim();
    if (!cleaned) {
      return "";
    }
    const parts = cleaned.split(".");
    if (parts.length < 4 || parts[0] !== "GA1") {
      return "";
    }
    return normalizeGa4ClientId(`${parts[2]}.${parts[3]}`);
  }

  function readCheckoutGa4Config() {
    const configEl = document.getElementById("checkout-ga4-client-config");
    if (!configEl) {
      return null;
    }
    try {
      return JSON.parse(configEl.textContent);
    } catch {
      return null;
    }
  }

  function sleep(ms) {
    return new Promise((resolve) => {
      window.setTimeout(resolve, ms);
    });
  }

  function gtagGet(measurementId, field, timeoutMs) {
    return new Promise((resolve) => {
      if (typeof gtag !== "function") {
        resolve("");
        return;
      }
      let settled = false;
      const timer = window.setTimeout(() => {
        if (!settled) {
          settled = true;
          resolve("");
        }
      }, timeoutMs);
      try {
        gtag("get", measurementId, field, (value) => {
          if (!settled) {
            settled = true;
            window.clearTimeout(timer);
            resolve(String(value ?? ""));
          }
        });
      } catch {
        window.clearTimeout(timer);
        resolve("");
      }
    });
  }

  async function readGa4FromGtag(config) {
    if (!config || !config.ga4MeasurementId) {
      return { clientId: "", sessionId: "" };
    }
    const timeoutMs = Number(config.gtagGetTimeoutMs) || 200;
    const measurementId = config.ga4MeasurementId;
    const [rawClientId, rawSessionId] = await Promise.all([
      gtagGet(measurementId, "client_id", timeoutMs),
      gtagGet(measurementId, "session_id", timeoutMs),
    ]);
    return {
      clientId: normalizeGa4ClientId(rawClientId),
      sessionId: normalizeGa4SessionId(rawSessionId),
    };
  }

  function readGa4FromCookies() {
    return {
      clientId: parseGaClientIdFromGaCookie(readCookieValue("_ga")),
      sessionId: "",
    };
  }

  function mergeGa4Identity(current, next) {
    return {
      clientId: current.clientId || next.clientId,
      sessionId: current.sessionId || next.sessionId,
    };
  }

  function hasMinimumCheckoutIdentity(ga4Identity, yandexClientId) {
    return Boolean(ga4Identity.clientId || yandexClientId);
  }

  function hasFullCheckoutIdentity(ga4Identity, yandexClientId) {
    return Boolean(ga4Identity.clientId && yandexClientId);
  }

  async function waitForCheckoutIdentity(config) {
    const budgetMs = Number(config?.identityWaitBudgetMs) || 2500;
    const intervalMs = Number(config?.identityPollIntervalMs) || 100;
    const deadline = Date.now() + budgetMs;
    let ga4Identity = readGa4FromCookies();
    let yandexClientId = readYandexClientId();

    while (Date.now() < deadline) {
      yandexClientId = readYandexClientId() || yandexClientId;
      ga4Identity = mergeGa4Identity(ga4Identity, readGa4FromCookies());
      if (config && (!ga4Identity.clientId || !ga4Identity.sessionId)) {
        ga4Identity = mergeGa4Identity(ga4Identity, await readGa4FromGtag(config));
      }
      if (hasFullCheckoutIdentity(ga4Identity, yandexClientId)) {
        break;
      }
      if (hasMinimumCheckoutIdentity(ga4Identity, yandexClientId) && Date.now() + intervalMs >= deadline) {
        break;
      }
      await sleep(intervalMs);
    }

    yandexClientId = readYandexClientId() || yandexClientId;
    ga4Identity = mergeGa4Identity(ga4Identity, readGa4FromCookies());
    return {
      yandexClientId,
      clientId: ga4Identity.clientId,
      sessionId: ga4Identity.sessionId,
    };
  }

  function applyCheckoutIdentityToForm(scope, identity) {
    const yandexClientIdInput = scope.querySelector("[data-checkout-yandex-client-id]");
    const ga4ClientIdInput = scope.querySelector("[data-checkout-ga4-client-id]");
    const ga4SessionIdInput = scope.querySelector("[data-checkout-ga4-session-id]");
    if (yandexClientIdInput) {
      yandexClientIdInput.value = identity.yandexClientId || "";
    }
    if (ga4ClientIdInput) {
      ga4ClientIdInput.value = identity.clientId || "";
    }
    if (ga4SessionIdInput) {
      ga4SessionIdInput.value = identity.sessionId || "";
    }
  }

  const checkoutScopes = Array.from(document.querySelectorAll("[data-checkout-scope]"));
  const checkoutGa4Config = readCheckoutGa4Config();

  checkoutScopes.forEach((scope) => {
    const emailInput = scope.querySelector("[data-checkout-email]");
    const emailError = scope.querySelector("[data-checkout-email-error]");
    const consentInput = scope.querySelector("[data-checkout-personal-data-consent]");
    const consentError = scope.querySelector("[data-checkout-personal-data-consent-error]");
    const submitButton = scope.querySelector("[data-checkout-submit]");
    const checkoutForm = scope.querySelector("form");
    const stepOneIcon = scope.querySelector('[data-checkout-step-icon="1"]');
    const stepTwoIcon = scope.querySelector('[data-checkout-step-icon="2"]');
    const stepOneDigit = scope.querySelector('[data-checkout-step-digit="1"]');
    const stepTwoDigit = scope.querySelector('[data-checkout-step-digit="2"]');

    if (
      !emailInput
      || !emailError
      || !submitButton
      || !checkoutForm
      || !stepOneIcon
      || !stepTwoIcon
      || !stepOneDigit
      || !stepTwoDigit
    ) {
      return;
    }

    let identityPrewarmPromise = null;

    const prewarmCheckoutIdentity = () => {
      if (!hasAnalyticsConsent()) {
        return null;
      }
      if (!identityPrewarmPromise) {
        identityPrewarmPromise = waitForCheckoutIdentity(checkoutGa4Config).then((identity) => {
          applyCheckoutIdentityToForm(scope, identity);
          return identity;
        });
      }
      return identityPrewarmPromise;
    };

    if (hasAnalyticsConsent()) {
      prewarmCheckoutIdentity();
    }

    const setStepActive = (step, isActive) => {
      const icon = step === 1 ? stepOneIcon : stepTwoIcon;
      const digit = step === 1 ? stepOneDigit : stepTwoDigit;

      icon.src = isActive ? icon.dataset.activeSrc : icon.dataset.inactiveSrc;
      digit.classList.toggle("text-[var(--color-white)]", isActive);
      digit.classList.toggle("text-[var(--color-black)]", !isActive);
    };

    const syncEmailState = () => {
      emailInput.setCustomValidity("");

      const hasValue = emailInput.value.trim().length > 0;
      const isValid = hasValue && emailInput.checkValidity();
      const showError = hasValue && !isValid;

      if (showError) {
        emailInput.setCustomValidity("Email");
      }

      emailError.classList.toggle("hidden", !showError);
      emailInput.style.borderColor = showError ? "#D45A5A" : "#C6C0C0";
      setStepActive(1, !isValid);
      setStepActive(2, isValid);

      return isValid;
    };

    const syncConsentState = () => {
      if (!consentInput) {
        return true;
      }
      if (consentInput.checked) {
        consentError?.classList.add("hidden");
        return true;
      }
      return false;
    };

    emailInput.addEventListener("input", syncEmailState);
    emailInput.addEventListener("blur", syncEmailState);

    if (consentInput) {
      consentInput.addEventListener("change", syncConsentState);
    }

    submitButton.addEventListener("click", () => {
      if (!syncEmailState()) {
        emailInput.focus();
        return;
      }
      if (consentInput && !consentInput.checked) {
        consentError?.classList.remove("hidden");
        consentInput.focus();
      }
    });

    checkoutForm.addEventListener("submit", (event) => {
      if (checkoutForm.dataset.checkoutSubmitting === "1") {
        return;
      }
      if (!syncEmailState()) {
        event.preventDefault();
        emailInput.focus();
        return;
      }
      if (consentInput && !consentInput.checked) {
        event.preventDefault();
        consentError?.classList.remove("hidden");
        consentInput.focus();
        return;
      }

      if (!hasAnalyticsConsent()) {
        applyCheckoutIdentityToForm(scope, { yandexClientId: "", clientId: "", sessionId: "" });
        submitButton.disabled = true;
        return;
      }

      event.preventDefault();
      submitButton.disabled = true;
      checkoutForm.dataset.checkoutSubmitting = "1";

      const identityReady = prewarmCheckoutIdentity() || waitForCheckoutIdentity(checkoutGa4Config);
      identityReady
        .then((identity) => {
          applyCheckoutIdentityToForm(scope, identity);
          checkoutForm.submit();
        })
        .catch(() => {
          applyCheckoutIdentityToForm(scope, {
            yandexClientId: readYandexClientId(),
            clientId: parseGaClientIdFromGaCookie(readCookieValue("_ga")),
            sessionId: "",
          });
          checkoutForm.submit();
        });
    });

    syncEmailState();
  });
});
