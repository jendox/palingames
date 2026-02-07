(function () {
  let lastOpener = null;

  function getDialogById(id) {
    const dlg = document.getElementById(id);
    return dlg && dlg.tagName === "DIALOG" ? dlg : null;
  }

  function getCookie(name) {
    const cookieString = document.cookie || "";
    for (const part of cookieString.split(";")) {
      const [key, ...rest] = part.trim().split("=");
      if (key === name) return decodeURIComponent(rest.join("="));
    }
    return null;
  }

  function getCsrfToken(form) {
    const cookieToken = getCookie("csrftoken");
    if (cookieToken) return cookieToken;

    const fromForm = form?.querySelector?.('input[name="csrfmiddlewaretoken"]')?.value;
    if (fromForm) return fromForm;

    const fromAnyForm = document.querySelector('input[name="csrfmiddlewaretoken"]')?.value;
    if (fromAnyForm) return fromAnyForm;

    return null;
  }

  function clearHeadlessFormState(form) {
    const errorsBox = form.closest("dialog")?.querySelector("[data-form-errors]");
    const successBox = form.closest("dialog")?.querySelector("[data-form-success]");
    const fieldErrors = form.querySelectorAll("[data-field-error]");

    for (const input of form.querySelectorAll("input")) {
      input.removeAttribute("aria-invalid");
      input.classList.remove("border-red-400");
    }

    for (const el of fieldErrors) {
      el.textContent = "";
      el.classList.add("hidden");
    }

    if (errorsBox) {
      errorsBox.textContent = "";
      errorsBox.classList.add("hidden");
    }
    if (successBox) {
      successBox.textContent = "";
      successBox.classList.add("hidden");
    }
  }

  function resetPasswordResetDialog(dlg) {
    const form = dlg.querySelector("form[data-headless-password-reset-form]");
    const keyForm = dlg.querySelector("form[data-headless-password-reset-key-form]");
    if (!form || !keyForm) return;

    const errorsBox = dlg.querySelector("[data-form-errors]");
    const successWrap = dlg.querySelector("[data-password-reset-success]");
    const successEmail = dlg.querySelector("[data-password-reset-email]");
    const completeWrap = dlg.querySelector("[data-password-reset-complete-success]");
    const requestSubtitle = dlg.querySelector("[data-password-reset-request-subtitle]");
    const keySubtitle = dlg.querySelector("[data-password-reset-key-subtitle]");

    if (errorsBox) {
      errorsBox.textContent = "";
      errorsBox.classList.add("hidden");
    }
    if (successEmail) {
      successEmail.textContent = "";
    }
    if (successWrap) {
      successWrap.classList.add("hidden");
    }
    if (completeWrap) {
      completeWrap.classList.add("hidden");
    }

    if (requestSubtitle) requestSubtitle.classList.remove("hidden");
    if (keySubtitle) keySubtitle.classList.add("hidden");

    form.classList.remove("hidden");
    keyForm.classList.add("hidden");

    const keyInput = keyForm.querySelector('input[name="key"]');
    if (keyInput) keyInput.value = "";

    clearHeadlessFormState(form);
    clearHeadlessFormState(keyForm);
    form.reset();
    keyForm.reset();
    setDialogSubmitting(form, false);
    setDialogSubmitting(keyForm, false);
  }

  function setDialogSubmitting(form, submitting) {
    for (const input of form.querySelectorAll("input, button")) {
      input.disabled = submitting;
    }
  }

  function setFieldError(form, fieldName, message) {
    const errorEl = form.querySelector(`[data-field-error="${fieldName}"]`);
    const input =
      form.querySelector(`input[name="${fieldName}"]`) ||
      (fieldName === "password1" ? form.querySelector('input[name="password1"]') : null);

    if (input) {
      input.setAttribute("aria-invalid", "true");
      input.classList.add("border-red-400");
    }
    if (errorEl) {
      errorEl.textContent = message;
      errorEl.classList.remove("hidden");
    }
  }

  function showDialogMessage(dlg, selector, message) {
    if (!dlg) return;
    const el = dlg.querySelector(selector);
    if (!el) return;
    el.textContent = message;
    el.classList.remove("hidden");
  }

  function openDialog(dlg, opener) {
    if (!dlg) return;
    lastOpener = opener || null;

    if (!dlg.open) dlg.showModal();

    for (const headlessForm of dlg.querySelectorAll(
      "form[data-headless-signup-form], form[data-headless-login-form]",
    )) {
      headlessForm.reset();
      setDialogSubmitting(headlessForm, false);
      clearHeadlessFormState(headlessForm);
    }

    resetPasswordResetDialog(dlg);

    // следующий тик, чтобы transition отработал
    requestAnimationFrame(() => {
      dlg.classList.remove("opacity-0", "scale-95");
      dlg.classList.add("opacity-100", "scale-100");
    });
  }

  function closeDialog(dlg) {
    if (!dlg) return;

    // запускаем анимацию закрытия
    dlg.classList.remove("opacity-100", "scale-100");
    dlg.classList.add("opacity-0", "scale-95");

    // ждём окончания transition и закрываем
    setTimeout(() => {
      if (dlg.open) dlg.close();
    }, 160);
  }

  async function verifyEmailKey(key) {
    const csrfToken = getCsrfToken();
    const resp = await fetch("/_allauth/browser/v1/auth/email/verify", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
      },
      body: JSON.stringify({ key }),
    });
    const payload = await resp.json().catch(() => null);
    return { resp, payload };
  }

  async function validatePasswordResetKey(key) {
    const resp = await fetch("/_allauth/browser/v1/auth/password/reset", {
      method: "GET",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "X-Password-Reset-Key": key,
      },
    });
    const payload = await resp.json().catch(() => null);
    return { resp, payload };
  }

  function setPasswordResetKeyFlow(dlg, key) {
    const requestForm = dlg.querySelector("form[data-headless-password-reset-form]");
    const keyForm = dlg.querySelector("form[data-headless-password-reset-key-form]");
    if (!requestForm || !keyForm) return;

    const successWrap = dlg.querySelector("[data-password-reset-success]");
    const completeWrap = dlg.querySelector("[data-password-reset-complete-success]");
    const errorsBox = dlg.querySelector("[data-form-errors]");
    const requestSubtitle = dlg.querySelector("[data-password-reset-request-subtitle]");
    const keySubtitle = dlg.querySelector("[data-password-reset-key-subtitle]");

    if (successWrap) successWrap.classList.add("hidden");
    if (completeWrap) completeWrap.classList.add("hidden");
    if (errorsBox) {
      errorsBox.textContent = "";
      errorsBox.classList.add("hidden");
    }
    if (requestSubtitle) requestSubtitle.classList.add("hidden");
    if (keySubtitle) keySubtitle.classList.remove("hidden");

    requestForm.classList.add("hidden");
    keyForm.classList.remove("hidden");

    const keyInput = keyForm.querySelector('input[name="key"]');
    if (keyInput) keyInput.value = key;

    clearHeadlessFormState(keyForm);
    setDialogSubmitting(keyForm, true);

    validatePasswordResetKey(key)
      .then(({ resp, payload }) => {
        const errors = payload && Array.isArray(payload.errors) ? payload.errors : [];
        if (!resp.ok || errors.length) {
          const messages = errors
            .map((err) => (typeof err?.message === "string" ? err.message : null))
            .filter(Boolean);
          resetPasswordResetDialog(dlg);
          if (errorsBox) {
            errorsBox.textContent = messages.length ? messages.join(" ") : "Некорректная или устаревшая ссылка для сброса пароля.";
            errorsBox.classList.remove("hidden");
          }
          return;
        }

        setDialogSubmitting(keyForm, false);
        const newPassword = keyForm.querySelector('input[name="password"]');
        if (newPassword instanceof HTMLInputElement) newPassword.focus();
      })
      .catch(() => {
        resetPasswordResetDialog(dlg);
        if (errorsBox) {
          errorsBox.textContent = "Не удалось проверить ссылку. Попробуйте позже.";
          errorsBox.classList.remove("hidden");
        }
      });
  }

  function openDialogFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const dialog = params.get("dialog");
    if (!dialog) return;

    const cleanUrl = () => {
      const clean = window.location.pathname + window.location.hash;
      window.history.replaceState({}, "", clean);
    };

    if (dialog === "signup") {
      openDialog(getDialogById("signupDialog"), null);
      cleanUrl();
      return;
    }

    if (dialog === "login") {
      const next = params.get("next");
      if (next && typeof next === "string" && next.startsWith("/")) {
        window.__postLoginRedirect = next;
        try {
          sessionStorage.setItem("postLoginRedirect", next);
        } catch {
          // ignore
        }
      }
      openDialog(getDialogById("loginDialog"), null);
      cleanUrl();
      return;
    }

    if (dialog === "password-reset") {
      const key = params.get("key");
      const dlg = getDialogById("passwordResetDialog");
      openDialog(dlg, null);
      if (key) {
        setPasswordResetKeyFlow(dlg, key);
      }
      cleanUrl();
      return;
    }

    if (dialog === "confirm-email") {
      const key = params.get("key");
      if (!key) {
        const dlg = getDialogById("signupDialog");
        openDialog(dlg, null);
        showDialogMessage(dlg, "[data-form-errors]", "Некорректная ссылка подтверждения email.");
        cleanUrl();
        return;
      }

      // Покажем модалку, чтобы было понятно, что что-то происходит.
      const dlg = getDialogById("signupDialog");
      openDialog(dlg, null);
      showDialogMessage(dlg, "[data-form-success]", "Подтверждаем email...");

      verifyEmailKey(key)
        .then(({ resp, payload }) => {
          const errors = payload && Array.isArray(payload.errors) ? payload.errors : [];
          if (errors.length) {
            const messages = errors
              .map((err) => (typeof err?.message === "string" ? err.message : null))
              .filter(Boolean);
            showDialogMessage(dlg, "[data-form-errors]", messages.length ? messages.join(" ") : "Не удалось подтвердить email.");
            return;
          }

          // Успех: allauth может вернуть 200 (залогинил) или 401 (просто подтвердил).
          if (resp.ok || resp.status === 401) {
            if (payload?.meta?.is_authenticated) {
              window.location.replace("/");
              return;
            }
            closeDialog(dlg);
            setTimeout(() => {
              openDialog(getDialogById("loginDialog"), null);
            }, 170);
            return;
          }

          showDialogMessage(dlg, "[data-form-errors]", "Не удалось подтвердить email. Попробуйте позже.");
        })
        .finally(() => {
          cleanUrl();
        });
      return;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    openDialogFromUrl();

    const loginDlg = getDialogById("loginDialog");
    if (loginDlg) {
      loginDlg.addEventListener("close", () => {
        try {
          sessionStorage.removeItem("postLoginRedirect");
        } catch {
          // ignore
        }
        try {
          delete window.__postLoginRedirect;
        } catch {
          // ignore
        }
      });
    }
  });

  document.addEventListener("click", (e) => {
    const mobileNavItem = e.target.closest?.("[data-mobile-nav-item]");
    if (mobileNavItem && mobileNavItem.closest?.("[data-mobile-bottom-nav]")) {
      mobileNavItem.classList.add("is-pending");

      const href = mobileNavItem instanceof HTMLAnchorElement ? mobileNavItem.getAttribute("href") : null;
      const isRealLink =
        href &&
        href !== "#" &&
        !href.startsWith("#") &&
        !href.toLowerCase().startsWith("javascript:");

      const hasModifiers = e.metaKey || e.ctrlKey || e.shiftKey || e.altKey;
      const opensDialog = !!mobileNavItem.closest?.("[data-dialog-open],[data-dialog-switch]");
      const target = mobileNavItem instanceof HTMLAnchorElement ? mobileNavItem.getAttribute("target") : null;
      const shouldHandleNav = isRealLink && !hasModifiers && !opensDialog && !target;

      if (shouldHandleNav) {
        e.preventDefault();
        requestAnimationFrame(() => {
          window.location.assign(href);
        });
        return;
      }

      // Если перехода нет (например, открываем модалку входа), не оставляем подсветку "навсегда".
      setTimeout(() => {
        mobileNavItem.classList.remove("is-pending");
      }, 180);
    }

    const switchBtn = e.target.closest("[data-dialog-switch]");
    if (switchBtn) {
      e.preventDefault();

      const id = switchBtn.getAttribute("data-dialog-switch");
      const fromDlg = switchBtn.closest("dialog");

      if (fromDlg && fromDlg.open) {
        closeDialog(fromDlg);
        setTimeout(() => {
          openDialog(getDialogById(id), switchBtn);
        }, 170);
        return;
      }

      openDialog(getDialogById(id), switchBtn);
      return;
    }

    const openBtn = e.target.closest("[data-dialog-open]");
    if (openBtn) {
      e.preventDefault();
      const id = openBtn.getAttribute("data-dialog-open");
      if (id === "loginDialog") {
        const redirectTo = openBtn.getAttribute("data-post-login-redirect");
        if (redirectTo) {
          window.__postLoginRedirect = redirectTo;
          try {
            sessionStorage.setItem("postLoginRedirect", redirectTo);
          } catch {
            // ignore
          }
        }
      }
      openDialog(getDialogById(id), openBtn);
      return;
    }

    const closeBtn = e.target.closest("[data-dialog-close]");
    if (closeBtn) {
      const dlg = closeBtn.closest("dialog");
      closeDialog(dlg);
      return;
    }

    // Закрытие по клику на backdrop:
    // у <dialog> клик по "серому" фону приходит как click по самому dialog
    const dlg = e.target.closest("dialog");
    if (dlg && dlg.open && e.target === dlg) {
      closeDialog(dlg);
    }
  });

  // Перехватываем ESC (cancel), чтобы закрытие было с анимацией
  document.addEventListener(
    "cancel",
    (e) => {
      const dlg = e.target;
      if (dlg && dlg.tagName === "DIALOG" && dlg.open) {
        e.preventDefault();
        closeDialog(dlg);
      }
    },
    true,
  );

  document.addEventListener("submit", async (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!form.matches("form[data-headless-logout-form]")) return;

    e.preventDefault();

    const submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;

    try {
      const csrfToken = getCsrfToken(form);
      const resp = await fetch("/_allauth/browser/v1/auth/session", {
        method: "DELETE",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
        },
      });

      if (resp.ok || resp.status === 401) {
        window.location.replace("/");
        return;
      }
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });

  document.addEventListener("click", (e) => {
    const link = e.target.closest?.("a[data-headless-logout-link]");
    if (!link) return;

    const form = link.closest("form[data-headless-logout-form]");
    if (!form) return;

    e.preventDefault();
    form.requestSubmit();
  });

  document.addEventListener("submit", async (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!form.matches('form[data-headless-signup-form]')) return;

    e.preventDefault();
    clearHeadlessFormState(form);

    const dlg = form.closest("dialog");
    if (!dlg) return;

    const endpoint = form.getAttribute("data-headless-endpoint");
    if (!endpoint) return;

    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }

    const email = (form.elements.namedItem("email")?.value || "").trim();
    const password1 = form.elements.namedItem("password1")?.value || "";
    const password2 = form.elements.namedItem("password2")?.value || "";

    if (password1 !== password2) {
      setFieldError(form, "password2", "Пароли не совпадают.");
      return;
    }

    setDialogSubmitting(form, true);

    try {
      const csrfToken = getCsrfToken(form);
      const resp = await fetch(endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
        },
        body: JSON.stringify({
          email,
          password: password1,
        }),
      });

      const payload = await resp.json().catch(() => null);

      const hasErrors = payload && Array.isArray(payload.errors) && payload.errors.length;
      if ((resp.ok || resp.status === 401) && !hasErrors) {
        showDialogMessage(
          dlg,
          "[data-form-success]",
          `Мы отправили письмо для подтверждения на ${email}. Подтвердите email, чтобы завершить регистрацию.`,
        );
        setDialogSubmitting(form, true);
        return;
      }

      const errors = payload && Array.isArray(payload.errors) ? payload.errors : [];
      const generalMessages = [];
      const fieldMessages = {
        email: [],
        password1: [],
        password2: [],
      };

      for (const err of errors) {
        const message = typeof err?.message === "string" ? err.message : "Ошибка.";
        const param = typeof err?.param === "string" ? err.param : null;

        if (!param || param === "__all__") {
          generalMessages.push(message);
          continue;
        }

        if (param === "email") fieldMessages.email.push(message);
        else if (param === "password") fieldMessages.password1.push(message);
        else if (param === "password1") fieldMessages.password1.push(message);
        else if (param === "password2") fieldMessages.password2.push(message);
        else generalMessages.push(message);
      }

      if (fieldMessages.email.length) setFieldError(form, "email", fieldMessages.email.join(" "));
      if (fieldMessages.password1.length) setFieldError(form, "password1", fieldMessages.password1.join(" "));
      if (fieldMessages.password2.length) setFieldError(form, "password2", fieldMessages.password2.join(" "));

      if (generalMessages.length) {
        showDialogMessage(dlg, "[data-form-errors]", generalMessages.join(" "));
      } else if (!errors.length) {
        showDialogMessage(dlg, "[data-form-errors]", "Не удалось отправить форму. Попробуйте ещё раз.");
      }

      setDialogSubmitting(form, false);
    } catch {
      showDialogMessage(dlg, "[data-form-errors]", "Не удалось отправить форму. Проверьте соединение и попробуйте ещё раз.");
      setDialogSubmitting(form, false);
    }
  });

  document.addEventListener("submit", async (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!form.matches('form[data-headless-login-form]')) return;

    e.preventDefault();
    clearHeadlessFormState(form);

    const dlg = form.closest("dialog");
    if (!dlg) return;

    const endpoint = form.getAttribute("data-headless-endpoint");
    if (!endpoint) return;

    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }

    const email = (form.elements.namedItem("email")?.value || "").trim();
    const password = form.elements.namedItem("password")?.value || "";
    // TODO(palingames): support "remember me" by passing a flag and adjusting session expiry server-side.

    setDialogSubmitting(form, true);

    try {
      const csrfToken = getCsrfToken(form);
      const resp = await fetch(endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
        },
        body: JSON.stringify({ email, password }),
      });

      const payload = await resp.json().catch(() => null);

      const errors = payload && Array.isArray(payload.errors) ? payload.errors : [];
      const metaIsAuthenticated = payload?.meta?.is_authenticated;

      // В разных версиях headless allauth успешный логин может прийти без meta/is_authenticated
      // (или даже без JSON body). Поэтому: если 2xx и ошибок нет, считаем успехом,
      // кроме случая когда сервер явно сказал is_authenticated=false.
      if (resp.ok && !errors.length && metaIsAuthenticated !== false) {
        let postLoginRedirect = null;
        try {
          postLoginRedirect = sessionStorage.getItem("postLoginRedirect");
          sessionStorage.removeItem("postLoginRedirect");
        } catch {
          // ignore
        }

        if (!postLoginRedirect) {
          const fromWindow = window.__postLoginRedirect;
          if (typeof fromWindow === "string" && fromWindow.startsWith("/")) {
            postLoginRedirect = fromWindow;
          }
        }

        if (!postLoginRedirect) {
          const fromOpener = lastOpener?.getAttribute?.("data-post-login-redirect");
          if (typeof fromOpener === "string" && fromOpener.startsWith("/")) {
            postLoginRedirect = fromOpener;
          }
        }

        try {
          delete window.__postLoginRedirect;
        } catch {
          // ignore
        }

        closeDialog(dlg);
        if (postLoginRedirect) {
          window.location.assign(postLoginRedirect);
        } else {
          window.location.reload();
        }
        return;
      }

      if ((resp.status === 401 || resp.ok) && !errors.length) {
        showDialogMessage(
          dlg,
          "[data-form-errors]",
          "Вход не завершён. Проверьте почту (подтверждение email) или попробуйте позже.",
        );
        setDialogSubmitting(form, false);
        return;
      }

      if (resp.status === 429) {
        showDialogMessage(dlg, "[data-form-errors]", "Слишком много попыток. Попробуйте позже.");
        setDialogSubmitting(form, false);
        return;
      }

      const generalMessages = [];
      const fieldMessages = {
        email: [],
        password: [],
      };

      for (const err of errors) {
        const message = typeof err?.message === "string" ? err.message : "Ошибка.";
        const param = typeof err?.param === "string" ? err.param : null;

        if (!param || param === "__all__") {
          generalMessages.push(message);
          continue;
        }

        if (param === "email") fieldMessages.email.push(message);
        else if (param === "password") fieldMessages.password.push(message);
        else generalMessages.push(message);
      }

      if (fieldMessages.email.length) setFieldError(form, "email", fieldMessages.email.join(" "));
      if (fieldMessages.password.length) setFieldError(form, "password", fieldMessages.password.join(" "));

      if (generalMessages.length) {
        showDialogMessage(dlg, "[data-form-errors]", generalMessages.join(" "));
      } else if (!errors.length) {
        showDialogMessage(dlg, "[data-form-errors]", "Не удалось выполнить вход. Попробуйте ещё раз.");
      }

      setDialogSubmitting(form, false);
    } catch {
      showDialogMessage(dlg, "[data-form-errors]", "Не удалось выполнить вход. Проверьте соединение и попробуйте ещё раз.");
      setDialogSubmitting(form, false);
    }
  });

  document.addEventListener("submit", async (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!form.matches('form[data-headless-password-reset-form]')) return;

    e.preventDefault();

    const dlg = form.closest("dialog");
    if (!dlg) return;

    const errorsBox = dlg.querySelector("[data-form-errors]");
    const successWrap = dlg.querySelector("[data-password-reset-success]");
    const successEmail = dlg.querySelector("[data-password-reset-email]");

    if (errorsBox) {
      errorsBox.textContent = "";
      errorsBox.classList.add("hidden");
    }

    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }

    const endpoint = form.getAttribute("data-headless-endpoint");
    if (!endpoint) return;

    const email = (form.elements.namedItem("email")?.value || "").trim();
    setDialogSubmitting(form, true);

    try {
      const csrfToken = getCsrfToken(form);
      const resp = await fetch(endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
        },
        body: JSON.stringify({ email }),
      });

      const payload = await resp.json().catch(() => null);

      if (resp.ok) {
        if (successEmail) successEmail.textContent = email;
        if (successWrap) successWrap.classList.remove("hidden");
        form.classList.add("hidden");
        setDialogSubmitting(form, false);
        return;
      }

      if (resp.status === 429) {
        if (errorsBox) {
          errorsBox.textContent = "Слишком много попыток. Попробуйте позже.";
          errorsBox.classList.remove("hidden");
        }
        setDialogSubmitting(form, false);
        return;
      }

      const errors = payload && Array.isArray(payload.errors) ? payload.errors : [];
      const messages = errors
        .map((err) => (typeof err?.message === "string" ? err.message : null))
        .filter(Boolean);

      if (errorsBox) {
        errorsBox.textContent = messages.length ? messages.join(" ") : "Не удалось отправить запрос. Попробуйте ещё раз.";
        errorsBox.classList.remove("hidden");
      }
      setDialogSubmitting(form, false);
    } catch {
      if (errorsBox) {
        errorsBox.textContent = "Не удалось отправить запрос. Проверьте соединение и попробуйте ещё раз.";
        errorsBox.classList.remove("hidden");
      }
      setDialogSubmitting(form, false);
    }
  });

  document.addEventListener("submit", async (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!form.matches('form[data-headless-password-reset-key-form]')) return;

    e.preventDefault();
    clearHeadlessFormState(form);

    const dlg = form.closest("dialog");
    if (!dlg) return;

    const errorsBox = dlg.querySelector("[data-form-errors]");
    const completeWrap = dlg.querySelector("[data-password-reset-complete-success]");

    if (errorsBox) {
      errorsBox.textContent = "";
      errorsBox.classList.add("hidden");
    }
    if (completeWrap) completeWrap.classList.add("hidden");

    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }

    const endpoint = form.getAttribute("data-headless-endpoint");
    if (!endpoint) return;

    const key = form.elements.namedItem("key")?.value || "";
    const password = form.elements.namedItem("password")?.value || "";
    const password2 = form.elements.namedItem("password2")?.value || "";

    if (!key) {
      if (errorsBox) {
        errorsBox.textContent = "Некорректная ссылка для сброса пароля.";
        errorsBox.classList.remove("hidden");
      }
      return;
    }

    if (password !== password2) {
      setFieldError(form, "password2", "Пароли не совпадают.");
      return;
    }

    setDialogSubmitting(form, true);

    try {
      const csrfToken = getCsrfToken(form);
      const resp = await fetch(endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
        },
        body: JSON.stringify({ key, password }),
      });

      const payload = await resp.json().catch(() => null);

      const errors = payload && Array.isArray(payload.errors) ? payload.errors : [];

      if (resp.ok) {
        if (payload?.meta?.is_authenticated) {
          window.location.replace("/");
          return;
        }
        if (completeWrap) completeWrap.classList.remove("hidden");
        form.classList.add("hidden");
        setDialogSubmitting(form, false);
        return;
      }

      if (resp.status === 429) {
        if (errorsBox) {
          errorsBox.textContent = "Слишком много попыток. Попробуйте позже.";
          errorsBox.classList.remove("hidden");
        }
        setDialogSubmitting(form, false);
        return;
      }

      // allauth headless may return 401 even if reset succeeded but user is not logged in
      if (resp.status === 401 && !errors.length) {
        if (completeWrap) completeWrap.classList.remove("hidden");
        form.classList.add("hidden");
        setDialogSubmitting(form, false);
        return;
      }

      const generalMessages = [];
      const fieldMessages = {
        key: [],
        password: [],
        password2: [],
      };

      for (const err of errors) {
        const message = typeof err?.message === "string" ? err.message : "Ошибка.";
        const param = typeof err?.param === "string" ? err.param : null;

        if (!param || param === "__all__") {
          generalMessages.push(message);
          continue;
        }

        if (param === "key") fieldMessages.key.push(message);
        else if (param === "password") fieldMessages.password.push(message);
        else if (param === "password2") fieldMessages.password2.push(message);
        else generalMessages.push(message);
      }

      if (fieldMessages.password.length) setFieldError(form, "password", fieldMessages.password.join(" "));
      if (fieldMessages.password2.length) setFieldError(form, "password2", fieldMessages.password2.join(" "));

      if (fieldMessages.key.length) {
        generalMessages.push(fieldMessages.key.join(" "));
      }

      if (errorsBox) {
        errorsBox.textContent = generalMessages.length ? generalMessages.join(" ") : "Не удалось сбросить пароль. Попробуйте ещё раз.";
        errorsBox.classList.remove("hidden");
      }
      setDialogSubmitting(form, false);
    } catch {
      if (errorsBox) {
        errorsBox.textContent = "Не удалось сбросить пароль. Проверьте соединение и попробуйте ещё раз.";
        errorsBox.classList.remove("hidden");
      }
      setDialogSubmitting(form, false);
    }
  });

  // Возврат фокуса на кнопку, которая открыла модалку
  document.addEventListener("close", (e) => {
    const dlg = e.target;
    if (dlg && dlg.tagName === "DIALOG" && lastOpener) {
      lastOpener.focus();
      lastOpener = null;
    }
  }, true);
})();
