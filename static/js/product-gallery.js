function initProductGallery(root) {
  const mainImage = root.querySelector("[data-gallery-main-image]");
  const prevButton = root.querySelector("[data-gallery-prev]");
  const nextButton = root.querySelector("[data-gallery-next]");
  const thumbButtons = Array.from(root.querySelectorAll("[data-gallery-thumb]"));
  const dotButtons = Array.from(root.querySelectorAll("[data-gallery-dot]"));
  const altText = root.dataset.galleryAlt || "";
  const autoplayMs = Number(root.dataset.galleryAutoplayMs || "5000");

  if (!mainImage) {
    return;
  }

  const imageSources = dotButtons.length
    ? dotButtons.map((button) => button.dataset.galleryImageUrl).filter(Boolean)
    : [
        mainImage.currentSrc || mainImage.src,
        ...thumbButtons.map((button) => button.dataset.galleryImageUrl).filter(Boolean),
      ];

  const images = Array.from(new Set(imageSources));

  if (!images.length) {
    return;
  }

  let activeIndex = 0;
  let autoplayId = null;

  function getThumbIndex(offset) {
    return (activeIndex + offset) % images.length;
  }

  function render() {
    mainImage.src = images[activeIndex];
    mainImage.alt = altText;

    thumbButtons.forEach((button, index) => {
      const imageIndex = getThumbIndex(index + 1);
      const thumbImage = button.querySelector("[data-gallery-thumb-image]");

      button.dataset.galleryThumb = String(imageIndex);
      button.dataset.galleryImageUrl = images[imageIndex];

      if (thumbImage) {
        thumbImage.src = images[imageIndex];
      }
    });

    dotButtons.forEach((button, index) => {
      const isActive = index === activeIndex;
      button.setAttribute("aria-pressed", isActive ? "true" : "false");
      button.style.backgroundColor = isActive ? "var(--color-purple)" : "#EECFF0";
    });
  }

  function stopAutoplay() {
    if (autoplayId !== null) {
      window.clearInterval(autoplayId);
      autoplayId = null;
    }
  }

  function startAutoplay() {
    stopAutoplay();

    if (autoplayMs > 0) {
      autoplayId = window.setInterval(() => {
        activeIndex = (activeIndex + 1) % images.length;
        render();
      }, autoplayMs);
    }
  }

  function goTo(index) {
    activeIndex = index;
    render();
    startAutoplay();
  }

  if (prevButton) {
    prevButton.addEventListener("click", () => {
      goTo((activeIndex - 1 + images.length) % images.length);
    });
  }

  if (nextButton) {
    nextButton.addEventListener("click", () => {
      goTo((activeIndex + 1) % images.length);
    });
  }

  root.addEventListener("click", (event) => {
    const dotButton = event.target.closest?.("[data-gallery-dot]");
    if (dotButton && root.contains(dotButton)) {
      const nextIndex = Number(dotButton.dataset.galleryDot);
      if (!Number.isNaN(nextIndex)) {
        goTo(nextIndex);
      }
      return;
    }

    const thumbButton = event.target.closest?.("[data-gallery-thumb]");
    if (thumbButton && root.contains(thumbButton)) {
      const nextIndex = Number(thumbButton.dataset.galleryThumb);
      if (!Number.isNaN(nextIndex)) {
        goTo(nextIndex);
      }
    }
  });

  if (autoplayMs > 0) {
    root.addEventListener("mouseenter", stopAutoplay);
    root.addEventListener("mouseleave", startAutoplay);
  }

  render();
  startAutoplay();
}

function initProductGalleries() {
  document.querySelectorAll("[data-product-gallery]").forEach(initProductGallery);
}

function initMobileDisclosures() {
  const disclosures = document.querySelectorAll("[data-mobile-disclosure]");

  function syncIcon(detailsEl) {
    const icon = detailsEl.querySelector("[data-mobile-disclosure-icon]");
    if (!icon) return;
    icon.style.transform = detailsEl.open ? "rotate(180deg)" : "rotate(-90deg)";
  }

  disclosures.forEach((detailsEl) => {
    syncIcon(detailsEl);
    detailsEl.addEventListener("toggle", () => {
      syncIcon(detailsEl);
    });
  });
}

function getCookie(name) {
  const cookieString = document.cookie || "";
  for (const part of cookieString.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) {
      return decodeURIComponent(rest.join("="));
    }
  }
  return null;
}

async function toggleCartOnServer(productId) {
  const csrfToken = getCookie("csrftoken");
  const body = new URLSearchParams({ product_id: String(productId) });

  const response = await fetch("/cart/toggle/", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Accept: "application/json",
      ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
    },
    body: body.toString(),
  });

  if (!response.ok) {
    throw new Error("Cart toggle failed");
  }

  return response.json();
}

async function copyTextToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return true;
  }

  const input = document.createElement("input");
  input.value = text;
  input.setAttribute("readonly", "");
  input.style.position = "absolute";
  input.style.left = "-9999px";
  document.body.appendChild(input);
  input.select();

  try {
    return document.execCommand("copy");
  } finally {
    input.remove();
  }
}

function toAbsoluteUrl(url) {
  try {
    return new URL(url, window.location.origin).href;
  } catch (_error) {
    return window.location.href;
  }
}

function initShareButtons() {
  const shareButtons = document.querySelectorAll("[data-copy-url-button]");

  shareButtons.forEach((button) => {
    if (button.dataset.copyUrlBound === "true") {
      return;
    }

    button.dataset.copyUrlBound = "true";

    button.addEventListener("click", async () => {
      const url = toAbsoluteUrl(button.dataset.copyUrl || window.location.href);
      const feedback = button.parentElement?.querySelector("[data-copy-url-feedback]");

      try {
        const copied = await copyTextToClipboard(url);
        if (!copied) {
          return;
        }

        if (feedback) {
          feedback.classList.remove("hidden");
          window.setTimeout(() => {
            feedback.classList.add("hidden");
          }, 1600);
        }
      } catch (_error) {
        // Ignore clipboard failures; the button remains non-destructive.
      }
    });
  });
}

function openProductAddedDialog(button) {
  const dialogId = button.dataset.productAddedDialog;
  if (!dialogId) {
    return;
  }

  const dialog = document.getElementById(dialogId);
  if (!(dialog instanceof HTMLDialogElement)) {
    return;
  }

  if (!dialog.open) {
    dialog.showModal();
  }

  requestAnimationFrame(() => {
    dialog.classList.remove("opacity-0", "scale-95");
    dialog.classList.add("opacity-100", "scale-100");
  });
}

function setProductFavoriteState(button, isFavorited) {
  button.dataset.favorited = isFavorited ? "true" : "false";
  button.setAttribute("aria-label", isFavorited ? "Удалить из избранного" : "Добавить в избранное");

  const icon = button.querySelector("[data-favorite-icon]");
  if (!(icon instanceof HTMLImageElement)) {
    return;
  }

  icon.src = isFavorited ? icon.dataset.iconActive : icon.dataset.iconInactive;
}

function initProductFavoriteButtons() {
  document.querySelectorAll("[data-product-favorite-toggle]").forEach((button) => {
    if (!(button instanceof HTMLElement) || button.dataset.favoriteBound === "true") {
      return;
    }

    button.dataset.favoriteBound = "true";
    setProductFavoriteState(button, button.dataset.favorited === "true");

    button.addEventListener("click", () => {
      const isFavorited = button.dataset.favorited === "true";
      setProductFavoriteState(button, !isFavorited);
    });
  });
}

function setProductCartState(button, isInCart) {
  button.dataset.inCart = isInCart ? "true" : "false";
}

function initProductCartButtons() {
  document.querySelectorAll("[data-product-cart-toggle]").forEach((button) => {
    if (!(button instanceof HTMLElement) || button.dataset.productCartBound === "true") {
      return;
    }

    button.dataset.productCartBound = "true";
    setProductCartState(button, button.dataset.inCart === "true");

    button.addEventListener("click", async () => {
      if (button.dataset.inCart === "true") {
        openProductAddedDialog(button);
        return;
      }

      if (button.dataset.cartPending === "true") {
        return;
      }

      const productId = Number.parseInt(button.dataset.productId || "", 10);
      if (!Number.isInteger(productId) || productId <= 0) {
        return;
      }

      button.dataset.cartPending = "true";
      button.setAttribute("aria-busy", "true");

      try {
        const payload = await toggleCartOnServer(productId);
        const isInCart = Boolean(payload.in_cart);
        setProductCartState(button, isInCart);
        if (isInCart) {
          openProductAddedDialog(button);
        }
      } catch (error) {
        console.error(error);
      } finally {
        button.dataset.cartPending = "false";
        button.removeAttribute("aria-busy");
      }
    });
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => {
    initProductGalleries();
    initMobileDisclosures();
    initShareButtons();
    initProductFavoriteButtons();
    initProductCartButtons();
  });
} else {
  initProductGalleries();
  initMobileDisclosures();
  initShareButtons();
  initProductFavoriteButtons();
  initProductCartButtons();
}
