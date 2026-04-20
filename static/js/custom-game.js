document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-custom-game-scope]").forEach((scope) => {
    initCustomGameScope(scope);
  });
});

function initCustomGameScope(scope) {
  const form = scope.querySelector("[data-custom-game-form]");
  const stars = Array.from(scope.querySelectorAll("[data-custom-game-star]"));
  const nextButtons = Array.from(scope.querySelectorAll("[data-custom-game-next]"));

  if (!form || stars.length === 0) {
    return;
  }

  let activeStep = 1;
  const stepCount = Math.max(
    ...stars.map((icon) => Number(icon.dataset.step)).filter((step) => !Number.isNaN(step)),
    1,
  );

  const setActiveStep = (step) => {
    activeStep = Math.min(Math.max(1, step), stepCount);
    syncStars();
  };

  const syncStars = () => {
    stars.forEach((icon) => {
      const step = Number(icon.dataset.step);
      if (Number.isNaN(step)) {
        return;
      }
      const mode = icon.dataset.customGameStarMode;
      const isActive = mode === "current" ? step === activeStep : step <= activeStep;
      const activeSrc = icon.dataset.activeSrc;
      const inactiveSrc = icon.dataset.inactiveSrc;
      if (activeSrc && inactiveSrc && icon instanceof HTMLImageElement) {
        icon.src = isActive ? activeSrc : inactiveSrc;
      }

      const digit = icon.parentElement?.querySelector("[data-custom-game-digit]");
      if (digit instanceof HTMLElement) {
        if (digit.dataset.activeColor || digit.dataset.inactiveColor) {
          digit.style.color = isActive
            ? (digit.dataset.activeColor || "")
            : (digit.dataset.inactiveColor || "");
        } else {
          digit.classList.toggle("text-[var(--color-white)]", isActive);
          digit.classList.toggle("text-[var(--color-black)]", !isActive);
        }
      }
    });
  };

  const findStepBlock = (element) => element?.closest?.("[data-custom-game-step-block]") ?? null;

  const validateStepBlock = (block) => {
    if (!block) {
      return true;
    }
    const fields = block.querySelectorAll("input, textarea, select");
    for (const field of fields) {
      if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement)) {
        continue;
      }
      if (field.hasAttribute("disabled") || field.type === "hidden") {
        continue;
      }
      field.setCustomValidity("");
      if (!field.checkValidity()) {
        field.reportValidity();
        return false;
      }
    }
    return true;
  };

  nextButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const block = findStepBlock(btn);
      if (!validateStepBlock(block)) {
        return;
      }
      const step = Number(block?.dataset.customGameStepBlock);
      if (!Number.isNaN(step)) {
        setActiveStep(step + 1);
      }
    });
  });

  form.querySelectorAll("[data-custom-game-step-block]").forEach((block) => {
    block.querySelectorAll("input, textarea, select").forEach((field) => {
      field.addEventListener("focus", () => {
        const n = Number(block.dataset.customGameStepBlock);
        if (!Number.isNaN(n)) {
          setActiveStep(n);
        }
      });
    });
  });

  const AUDIENCE_OTHER_VALUE = "other";
  const audiencePresetFields = form.querySelectorAll('[name="audience_preset"]');
  const audienceOtherInput = form.querySelector('[name="audience_other"]');
  const audienceOtherBlock = form.querySelector("[data-custom-game-audience-other-block]");

  const syncAudienceOther = () => {
    const selected = form.querySelector('[name="audience_preset"]:checked');
    const isOther = selected?.value === AUDIENCE_OTHER_VALUE;
    if (audienceOtherInput instanceof HTMLInputElement) {
      audienceOtherInput.required = Boolean(isOther);
    }
    if (audienceOtherBlock instanceof HTMLElement) {
      audienceOtherBlock.classList.toggle("hidden", !isOther);
    }
  };

  audiencePresetFields.forEach((field) => {
    field.addEventListener("change", syncAudienceOther);
  });
  syncAudienceOther();

  form.addEventListener("submit", (event) => {
    const lastBlock = scope.querySelector(`[data-custom-game-step-block="${stepCount}"]`);
    if (!validateStepBlock(lastBlock)) {
      event.preventDefault();
      return;
    }
    if (!form.checkValidity()) {
      event.preventDefault();
      form.reportValidity();
    }
  });

  syncStars();
}
