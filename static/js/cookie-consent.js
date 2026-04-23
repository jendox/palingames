(function () {
  const COOKIE_NAME = "pali_consent";

  function getConfig() {
    const el = document.getElementById("cookie-consent-config");
    if (!el) {
      return null;
    }
    try {
      return JSON.parse(el.textContent);
    } catch {
      return null;
    }
  }

  function getCsrfToken() {
    const parts = (document.cookie || "").split(";").map((c) => c.trim());
    for (const part of parts) {
      if (part.startsWith("csrftoken=")) {
        return decodeURIComponent(part.slice("csrftoken=".length));
      }
    }
    return "";
  }

  function parseStoredConsent(config) {
    const raw = document.cookie
      .split(";")
      .map((c) => c.trim())
      .find((c) => c.startsWith(`${COOKIE_NAME}=`));
    if (!raw) {
      return null;
    }
    const encoded = raw.slice(`${COOKIE_NAME}=`.length);
    let parsed;
    try {
      parsed = JSON.parse(decodeURIComponent(encoded));
    } catch {
      return null;
    }
    if (typeof parsed !== "object" || parsed === null) {
      return null;
    }
    const version = parsed.v;
    const analytics = parsed.a;
    if (typeof version !== "number" || typeof analytics !== "boolean") {
      return null;
    }
    if (version !== config.policyVersion) {
      return null;
    }
    return { analyticsStorage: analytics };
  }

  function writeConsentCookie(config, analyticsStorage) {
    const payload = encodeURIComponent(
      JSON.stringify({
        v: config.policyVersion,
        a: analyticsStorage,
      }),
    );
    const secure = window.location.protocol === "https:" ? ";Secure" : "";
    document.cookie = `${COOKIE_NAME}=${payload};Path=/;Max-Age=${config.maxAgeSeconds};SameSite=Lax${secure}`;
  }

  function syncServer(config, analyticsStorage) {
    return fetch(config.consentApiUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),
      },
      body: JSON.stringify({
        analytics_storage: analyticsStorage,
        policy_version: config.policyVersion,
      }),
    });
  }

  function loadGtm(gtmId) {
    if (window.__paliGtmLoaded) {
      return;
    }
    window.__paliGtmLoaded = true;
    (function (w, d, s, l, i) {
      w[l] = w[l] || [];
      w[l].push({ "gtm.start": new Date().getTime(), event: "gtm.js" });
      const f = d.getElementsByTagName(s)[0];
      const j = d.createElement(s);
      const dl = l !== "dataLayer" ? "&l=" + l : "";
      j.async = true;
      j.src = "https://www.googletagmanager.com/gtm.js?id=" + i + dl;
      f.parentNode.insertBefore(j, f);
    })(window, document, "script", "dataLayer", gtmId);

    const nos = document.createElement("noscript");
    const iframe = document.createElement("iframe");
    iframe.src = "https://www.googletagmanager.com/ns.html?id=" + encodeURIComponent(gtmId);
    iframe.height = "0";
    iframe.width = "0";
    iframe.style.display = "none";
    iframe.style.visibility = "hidden";
    nos.appendChild(iframe);
    document.body.insertBefore(nos, document.body.firstChild);
  }

  function loadAnalytics(config) {
    if (window.__paliAnalyticsJsLoaded) {
      return;
    }
    window.__paliAnalyticsJsLoaded = true;
    const s = document.createElement("script");
    s.src = config.analyticsJsUrl;
    s.defer = true;
    document.head.appendChild(s);
  }

  function applyConsentGranted(config) {
    if (typeof gtag === "function") {
      gtag("consent", "update", {
        analytics_storage: "granted",
        ad_storage: "granted",
        ad_user_data: "granted",
        ad_personalization: "granted",
      });
    }
    loadGtm(config.gtmId);
    loadAnalytics(config);
  }

  function applyConsentDenied() {
    if (typeof gtag === "function") {
      gtag("consent", "update", {
        analytics_storage: "denied",
        ad_storage: "denied",
        ad_user_data: "denied",
        ad_personalization: "denied",
      });
    }
  }

  function initUi(panel) {
    function showPanel() {
      panel.classList.remove("hidden");
      panel.setAttribute("aria-hidden", "false");
    }

    function hidePanel() {
      panel.classList.add("hidden");
      panel.setAttribute("aria-hidden", "true");
    }

    window.PaliCookieConsent = {
      openSettings() {
        showPanel();
      },
    };

    document.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) {
        return;
      }
      const opener = target.closest("[data-open-cookie-consent]");
      if (opener) {
        event.preventDefault();
        showPanel();
      }
    });

    return { showPanel, hidePanel };
  }

  function bindActions(config, ui) {
    const accept = document.getElementById("cookie-consent-accept");
    const reject = document.getElementById("cookie-consent-reject");
    if (accept && !accept.dataset.bound) {
      accept.dataset.bound = "1";
      accept.addEventListener("click", () => {
        writeConsentCookie(config, true);
        applyConsentGranted(config);
        syncServer(config, true).catch(() => {});
        ui.hidePanel();
      });
    }
    if (reject && !reject.dataset.bound) {
      reject.dataset.bound = "1";
      reject.addEventListener("click", () => {
        writeConsentCookie(config, false);
        applyConsentDenied();
        syncServer(config, false).catch(() => {});
        ui.hidePanel();
      });
    }
  }

  function bootstrap() {
    const config = getConfig();
    if (!config) {
      return;
    }

    const panel = document.getElementById("cookie-consent-panel");
    if (!panel) {
      return;
    }

    const { showPanel, hidePanel } = initUi(panel);

    const stored = parseStoredConsent(config);
    if (stored && stored.analyticsStorage === true) {
      applyConsentGranted(config);
      syncServer(config, true).catch(() => {});
      hidePanel();
      bindActions(config, { showPanel, hidePanel });
      return;
    }
    if (stored && stored.analyticsStorage === false) {
      applyConsentDenied();
      syncServer(config, false).catch(() => {});
      hidePanel();
      bindActions(config, { showPanel, hidePanel });
      return;
    }

    showPanel();
    bindActions(config, { showPanel, hidePanel });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap);
  } else {
    bootstrap();
  }
})();
