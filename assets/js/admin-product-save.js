(function () {
  const form = document.getElementById("product_form");
  if (!form) return;

  const statusEl = document.getElementById("product-admin-save-status");
  let submitting = false;

  function showSavingStatus() {
    if (!statusEl) return;
    statusEl.hidden = false;
    statusEl.textContent = "Сохраняем товар и загружаем изображения…";
    statusEl.style.color = "#417690";
    statusEl.style.fontWeight = "600";
    statusEl.style.margin = "0 0 12px 0";
  }

  function disableSubmitButtons() {
    form.querySelectorAll('input[type="submit"], button[type="submit"]').forEach((button) => {
      button.disabled = true;
    });
  }

  function preserveSubmitterValue(submitter) {
    if (!submitter || !submitter.name) return;

    const selector = `input[type="hidden"][data-product-admin-submit="${submitter.name}"]`;
    let hiddenInput = form.querySelector(selector);
    if (!hiddenInput) {
      hiddenInput = document.createElement("input");
      hiddenInput.type = "hidden";
      hiddenInput.dataset.productAdminSubmit = submitter.name;
      form.appendChild(hiddenInput);
    }
    hiddenInput.name = submitter.name;
    hiddenInput.value = submitter.value;
  }

  form.addEventListener("submit", (event) => {
    if (submitting) {
      event.preventDefault();
      return;
    }

    if (typeof form.checkValidity === "function" && !form.checkValidity()) {
      return;
    }

    submitting = true;
    preserveSubmitterValue(event.submitter);
    disableSubmitButtons();
    showSavingStatus();
  });
})();
