document.addEventListener("DOMContentLoaded", () => {
  const emailInput = document.querySelector("[data-checkout-email]");
  const emailError = document.querySelector("[data-checkout-email-error]");
  const submitButton = document.querySelector("[data-checkout-submit]");
  const stepOneIcon = document.querySelector('[data-checkout-step-icon="1"]');
  const stepTwoIcon = document.querySelector('[data-checkout-step-icon="2"]');
  const stepOneDigit = document.querySelector('[data-checkout-step-digit="1"]');
  const stepTwoDigit = document.querySelector('[data-checkout-step-digit="2"]');
  const createdDialog = document.getElementById("checkoutOrderCreatedDialog");

  if (!emailInput || !emailError || !submitButton || !stepOneIcon || !stepTwoIcon || !stepOneDigit || !stepTwoDigit || !createdDialog) {
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
    if (syncEmailState()) {
      if (!createdDialog.open) {
        createdDialog.showModal();
      }

      requestAnimationFrame(() => {
        createdDialog.classList.remove("opacity-0", "scale-95");
        createdDialog.classList.add("opacity-100", "scale-100");
      });
      return;
    }

    emailInput.focus();
  });

  syncEmailState();
});
