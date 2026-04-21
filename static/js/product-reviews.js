(function () {
  const REVIEW_SUBMITTED_MESSAGE = "Отзыв отправлен на модерацию.";

  function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) {
      return parts.pop().split(";").shift();
    }
    return null;
  }

  function paintStars(starContainer, value) {
    const filledSrc = starContainer.dataset.starFilledSrc;
    const emptySrc = starContainer.dataset.starEmptySrc;
    const buttons = starContainer.querySelectorAll("[data-review-star]");
    buttons.forEach(function (button) {
      const v = parseInt(button.getAttribute("data-rating-value"), 10);
      const img = button.querySelector("img");
      if (!img) {
        return;
      }
      const filled = filledSrc || img.src;
      const empty = emptySrc || img.src;
      img.src = v <= value ? filled : empty;
    });
  }

  function showReviewSubmittedNotification() {
    const notify = window.PaliGamesDownloads?.showNotification;
    if (typeof notify === "function") {
      notify(REVIEW_SUBMITTED_MESSAGE);
      return;
    }
    window.alert(REVIEW_SUBMITTED_MESSAGE);
  }

  function initRating(scope) {
    const form = scope.querySelector("form[data-review-form]");
    if (!form) {
      return;
    }
    const hidden = form.querySelector("[data-review-rating-input]");
    const starContainer = form.querySelector("[data-review-stars]");
    if (!hidden || !starContainer) {
      return;
    }

    paintStars(starContainer, parseInt(hidden.value, 10) || 5);

    starContainer.addEventListener("click", function (e) {
      const star = e.target.closest("[data-review-star]");
      if (!star) {
        return;
      }
      const v = parseInt(star.getAttribute("data-rating-value"), 10);
      hidden.value = String(v);
      paintStars(starContainer, v);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.body.addEventListener("htmx:configRequest", function (event) {
      const token = getCookie("csrftoken");
      if (token) {
        event.detail.headers["X-CSRFToken"] = token;
      }
    });

    document.querySelectorAll("#product-review-panel").forEach(initRating);
  });

  document.body.addEventListener("htmx:afterSwap", function (event) {
    const t = event.target;
    if (!t) {
      return;
    }
    if (t.id === "product-review-panel") {
      initRating(t);
      return;
    }
    const reviewPanel = t.querySelector?.("#product-review-panel");
    if (reviewPanel) {
      initRating(reviewPanel);
    }
  });

  document.body.addEventListener("review:submitted", function () {
    showReviewSubmittedNotification();
  });
})();
