document.addEventListener("DOMContentLoaded", () => {
  const createdDialog = document.getElementById("checkoutOrderCreatedDialog");
  if (!createdDialog || createdDialog.dataset.checkoutOrderCreated !== "true") {
    return;
  }

  const closeDelayMs = Number(createdDialog.dataset.checkoutCloseDelayMs || "0");
  createdDialog.dataset.checkoutCanClose = "false";
  createdDialog.showModal();
  requestAnimationFrame(() => {
    createdDialog.classList.remove("opacity-0", "scale-95");
    createdDialog.classList.add("opacity-100", "scale-100");
  });
  window.setTimeout(() => {
    createdDialog.dataset.checkoutCanClose = "true";
  }, closeDelayMs);

  const closeCreatedDialog = () => {
    if (createdDialog.open) {
      createdDialog.dataset.checkoutClosedByUser = "true";
      createdDialog.close("user");
    }
  };

  createdDialog.addEventListener(
    "click",
    (event) => {
      if (createdDialog.dataset.checkoutCanClose !== "true") {
        event.stopPropagation();
        event.preventDefault();
        return;
      }

      const closeButton = event.target.closest("[data-checkout-dialog-close]");
      if (closeButton) {
        event.preventDefault();
        closeCreatedDialog();
        return;
      }

      if (event.target === createdDialog) {
        event.preventDefault();
        closeCreatedDialog();
      }
    },
    true,
  );

  createdDialog.addEventListener(
    "cancel",
    (event) => {
      if (createdDialog.dataset.checkoutCanClose !== "true") {
        event.preventDefault();
        return;
      }

      event.preventDefault();
      closeCreatedDialog();
    },
    true,
  );

  createdDialog.addEventListener("close", () => {
    if (
      createdDialog.dataset.checkoutOrderCreated !== "true"
      || createdDialog.dataset.checkoutClosedByUser !== "true"
    ) {
      return;
    }

    delete createdDialog.dataset.checkoutClosedByUser;
    const redirectUrl = (createdDialog.dataset.checkoutRedirectUrl || "").trim();
    if (redirectUrl) {
      window.location.assign(redirectUrl);
    }
  });
});
