document.addEventListener("DOMContentLoaded", () => {
  const checkoutScopes = Array.from(document.querySelectorAll("[data-checkout-scope]"));

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

      submitButton.disabled = true;
    });

    syncEmailState();
  });
});
