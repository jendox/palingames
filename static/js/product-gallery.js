function initProductGallery(root) {
  const mainImage = root.querySelector("[data-gallery-main-image]");
  const prevButton = root.querySelector("[data-gallery-prev]");
  const nextButton = root.querySelector("[data-gallery-next]");
  const thumbButtons = Array.from(root.querySelectorAll("[data-gallery-thumb]"));
  const altText = root.dataset.galleryAlt || "";
  const autoplayMs = Number(root.dataset.galleryAutoplayMs || "5000");

  if (!mainImage || !prevButton || !nextButton || thumbButtons.length !== 2) {
    return;
  }

  const images = [
    mainImage.currentSrc || mainImage.src,
    ...thumbButtons.map((button) => button.dataset.galleryImageUrl).filter(Boolean),
  ];

  if (images.length !== 3) {
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

  prevButton.addEventListener("click", () => {
    goTo((activeIndex - 1 + images.length) % images.length);
  });

  nextButton.addEventListener("click", () => {
    goTo((activeIndex + 1) % images.length);
  });

  thumbButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const nextIndex = Number(button.dataset.galleryThumb);
      if (!Number.isNaN(nextIndex)) {
        goTo(nextIndex);
      }
    });
  });

  root.addEventListener("mouseenter", stopAutoplay);
  root.addEventListener("mouseleave", startAutoplay);

  render();
  startAutoplay();
}

function initProductGalleries() {
  document.querySelectorAll("[data-product-gallery]").forEach(initProductGallery);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initProductGalleries);
} else {
  initProductGalleries();
}
