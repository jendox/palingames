document.addEventListener("DOMContentLoaded", () => {
  const checkoutScopes = Array.from(document.querySelectorAll("[data-checkout-scope]"));
  const activeScope =
    checkoutScopes.find((scope) => scope.offsetParent !== null) || checkoutScopes[0] || null;

  if (!activeScope) {
    return;
  }

  const emailInput = activeScope.querySelector("[data-checkout-email]");
  const emailError = activeScope.querySelector("[data-checkout-email-error]");
  const submitButton = activeScope.querySelector("[data-checkout-submit]");
  const checkoutForm = submitButton?.closest("form");
  const stepOneIcon = activeScope.querySelector('[data-checkout-step-icon="1"]');
  const stepTwoIcon = activeScope.querySelector('[data-checkout-step-icon="2"]');
  const stepOneDigit = activeScope.querySelector('[data-checkout-step-digit="1"]');
  const stepTwoDigit = activeScope.querySelector('[data-checkout-step-digit="2"]');
  const createdDialog = document.getElementById("checkoutOrderCreatedDialog");

  if (
    !emailInput
    || !emailError
    || !submitButton
    || !checkoutForm
    || !stepOneIcon
    || !stepTwoIcon
    || !stepOneDigit
    || !stepTwoDigit
    || !createdDialog
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

  emailInput.addEventListener("input", syncEmailState);
  emailInput.addEventListener("blur", syncEmailState);

  submitButton.addEventListener("click", () => {
    if (!syncEmailState()) {
      emailInput.focus();
    }
  });

  checkoutForm.addEventListener("submit", (event) => {
    if (syncEmailState()) {
      submitButton.disabled = true;
      return;
    }

    event.preventDefault();
    emailInput.focus();
  });

  syncEmailState();
});
