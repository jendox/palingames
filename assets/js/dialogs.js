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
    const el = dlg.querySelector(selector);
    if (!el) return;
    el.textContent = message;
    el.classList.remove("hidden");
  }

  function openDialog(dlg, opener) {
    if (!dlg) return;
    lastOpener = opener || null;

    if (!dlg.open) dlg.showModal();

    const headlessForm = dlg.querySelector("form[data-headless-signup-form]");
    if (headlessForm) {
      headlessForm.reset();
      setDialogSubmitting(headlessForm, false);
      clearHeadlessFormState(headlessForm);
    }

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

  document.addEventListener("click", (e) => {
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
      const csrfToken = getCookie("csrftoken");
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

      if (resp.ok) {
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

  // Возврат фокуса на кнопку, которая открыла модалку
  document.addEventListener("close", (e) => {
    const dlg = e.target;
    if (dlg && dlg.tagName === "DIALOG" && lastOpener) {
      lastOpener.focus();
      lastOpener = null;
    }
  }, true);
})();
