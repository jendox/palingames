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

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => {
    initProductGalleries();
    initMobileDisclosures();
  });
} else {
  initProductGalleries();
  initMobileDisclosures();
}
