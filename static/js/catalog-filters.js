function sanitizeFloatInputValue(value) {
  let result = "";
  let hasDecimalSeparator = false;

  for (const rawChar of value) {
    const char = rawChar === "." ? "," : rawChar;

    if (char >= "0" && char <= "9") {
      result += char;
      continue;
    }

    if (char === "," && !hasDecimalSeparator) {
      result += char;
      hasDecimalSeparator = true;
    }
  }

  return result;
}

function parseCommaFloat(value) {
  const normalized = sanitizeFloatInputValue(value).replace(",", ".");
  const parsed = Number.parseFloat(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatCommaFloat(value) {
  return value.toFixed(2).replace(".", ",");
}

function initCatalogFloatInputs(root = document) {
  const inputs = root.querySelectorAll("[data-float-input]");

  inputs.forEach((input) => {
    if (input.dataset.floatInputBound === "true") {
      return;
    }

    input.dataset.floatInputBound = "true";

    input.addEventListener("input", () => {
      const sanitizedValue = sanitizeFloatInputValue(input.value);

      if (input.value !== sanitizedValue) {
        input.value = sanitizedValue;
      }
    });

    input.addEventListener("paste", (event) => {
      event.preventDefault();

      const pastedText = event.clipboardData?.getData("text") || "";
      const sanitizedValue = sanitizeFloatInputValue(pastedText);

      if (!sanitizedValue) {
        return;
      }

      input.setRangeText(
        sanitizedValue,
        input.selectionStart ?? input.value.length,
        input.selectionEnd ?? input.value.length,
        "end",
      );
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });

    input.addEventListener("blur", () => {
      const parsedValue = parseCommaFloat(input.value);
      const minValue = parseCommaFloat(input.dataset.minPrice || "");
      const maxValue = parseCommaFloat(input.dataset.maxPrice || "");

      if (parsedValue === null) {
        input.value = "";
        return;
      }

      let normalizedValue = parsedValue;

      if (minValue !== null) {
        normalizedValue = Math.max(normalizedValue, minValue);
      }

      if (maxValue !== null) {
        normalizedValue = Math.min(normalizedValue, maxValue);
      }

      input.value = formatCommaFloat(normalizedValue);
    });
  });
}

function initCatalogDropdowns(root = document) {
  const dropdowns = root.querySelectorAll("[data-catalog-dropdown]");

  dropdowns.forEach((dropdown) => {
    if (dropdown.dataset.catalogDropdownBound === "true") {
      return;
    }

    dropdown.dataset.catalogDropdownBound = "true";

    dropdown.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !dropdown.hasAttribute("open")) {
        return;
      }

      event.preventDefault();
      event.stopPropagation();
      dropdown.removeAttribute("open");

      const summary = dropdown.querySelector("summary");
      if (summary instanceof HTMLElement) {
        summary.blur();
      }
    });
  });
}

function updateCatalogSortState(selectedValue) {
  document.querySelectorAll("[data-catalog-sort-input]").forEach((sortInput) => {
    if (!(sortInput instanceof HTMLInputElement)) {
      return;
    }

    sortInput.value = selectedValue || "";
    sortInput.disabled = !selectedValue;
  });

  document.querySelectorAll("[data-sort-option]").forEach((option) => {
    const checkbox = option.querySelector(".checkbox-purple");
    if (!(checkbox instanceof HTMLElement)) {
      return;
    }

    checkbox.classList.toggle("checked", option.dataset.sortValue === selectedValue);
  });
}

function initCatalogSortControls(root = document) {
  root.querySelectorAll("[data-sort-option]").forEach((option) => {
    if (option.dataset.sortBound === "true") {
      return;
    }

    option.dataset.sortBound = "true";
    option.addEventListener("click", () => {
      updateCatalogSortState(option.dataset.sortValue || "");

      const mobileFilterForm = option.closest("[data-catalog-mobile-filter-form]");
      if (mobileFilterForm instanceof HTMLFormElement) {
        mobileFilterForm.dataset.filterDirty = "true";
        return;
      }

      option.closest("details")?.removeAttribute("open");
    });
  });

  root.querySelectorAll("[data-sort-reset]").forEach((control) => {
    if (control.dataset.sortResetBound === "true") {
      return;
    }

    control.dataset.sortResetBound = "true";
    control.addEventListener("click", () => {
      updateCatalogSortState("");

      const mobileFilterForm = control.closest("[data-catalog-mobile-filter-form]");
      if (mobileFilterForm instanceof HTMLFormElement) {
        mobileFilterForm.dataset.filterDirty = "true";
        return;
      }

      control.closest("details")?.removeAttribute("open");
    });
  });
}

function initCatalogFilterReset(root = document) {
  root.querySelectorAll("[data-filter-reset]").forEach((button) => {
    if (button.dataset.filterResetBound === "true") {
      return;
    }

    button.dataset.filterResetBound = "true";
    button.addEventListener("click", () => {
      const form = button.closest("[data-catalog-filter-form]");
      if (!(form instanceof HTMLFormElement)) {
        return;
      }

      form.querySelectorAll('input[type="checkbox"]').forEach((input) => {
        input.checked = false;
      });

      form.querySelectorAll("[data-float-input]").forEach((input) => {
        input.value = input.dataset.defaultValue || "";
      });

      if (form.hasAttribute("data-catalog-mobile-filter-form")) {
        form.querySelectorAll("[data-catalog-sort-input]").forEach((input) => {
          if (input instanceof HTMLInputElement) {
            input.value = "";
            input.disabled = true;
          }
        });
        updateCatalogSortState("");
        form.dataset.filterDirty = "true";
        return;
      }

      if (window.htmx) {
        window.htmx.trigger(form, "submit");
        return;
      }

      form.requestSubmit();
    });
  });
}

function initCatalogMobileFilterDialog(root = document) {
  root.querySelectorAll("dialog").forEach((dialog) => {
    if (!(dialog instanceof HTMLDialogElement) || dialog.dataset.catalogMobileFilterBound === "true") {
      return;
    }

    const form = dialog.querySelector("[data-catalog-mobile-filter-form]");
    if (!(form instanceof HTMLFormElement)) {
      return;
    }

    dialog.dataset.catalogMobileFilterBound = "true";

    form.addEventListener("change", () => {
      form.dataset.filterDirty = "true";
    });

    form.addEventListener("input", () => {
      form.dataset.filterDirty = "true";
    });

    form.addEventListener("submit", () => {
      delete form.dataset.filterDirty;
    });

    dialog.addEventListener("close", () => {
      if (form.dataset.filterDirty !== "true") {
        return;
      }

      delete form.dataset.filterDirty;

      if (window.htmx) {
        window.htmx.trigger(form, "submit");
        return;
      }

      form.requestSubmit();
    });
  });
}

function setFavoriteState(button, isFavorited) {
  button.dataset.favorited = isFavorited ? "true" : "false";
  button.setAttribute("aria-label", isFavorited ? "Удалить из избранного" : "Добавить в избранное");

  const icon = button.querySelector("[data-favorite-icon]");
  if (!(icon instanceof HTMLImageElement)) {
    return;
  }

  icon.src = isFavorited ? icon.dataset.iconActive : icon.dataset.iconInactive;
}

function setCartState(button, isInCart) {
  const isPurchased = button.dataset.isPurchased === "true";
  button.dataset.inCart = isInCart ? "true" : "false";
  button.classList.toggle("catalog-card-cart-button-active", isInCart);
  if (button.hasAttribute("data-cart-border-toggle")) {
    button.classList.toggle("border", !isInCart && !isPurchased);
  }
  button.setAttribute(
    "aria-label",
    isPurchased ? "Скачать" : (isInCart ? "Удалить из корзины" : "Добавить в корзину"),
  );

  const icon = button.querySelector("[data-cart-icon]");
  if (!(icon instanceof HTMLImageElement)) {
    return;
  }

  if (isPurchased) {
    icon.src = icon.dataset.iconDownload;
  } else {
    icon.src = isInCart ? icon.dataset.iconFull : icon.dataset.iconEmpty;
  }

  const productId = Number.parseInt(button.dataset.productId || "", 10);
  if (Number.isInteger(productId) && productId > 0) {
    document.body.dispatchEvent(
      new CustomEvent("catalog:cart-updated", {
        detail: { productId, inCart: isInCart },
      }),
    );
  }
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

function getCatalogPreviewDialog() {
  const dialog = document.getElementById("catalogProductPreviewDialog");
  return dialog instanceof HTMLDialogElement ? dialog : null;
}

function closeCatalogPreviewDialog(dialog) {
  dialog.classList.remove("opacity-100", "scale-100");
  dialog.classList.add("opacity-0", "scale-95");

  window.setTimeout(() => {
    if (dialog.open) {
      dialog.close();
    }
  }, 150);
}

function openCatalogPreviewDialog(trigger) {
  const dialog = getCatalogPreviewDialog();
  if (!dialog) {
    return;
  }

  const image = dialog.querySelector("[data-catalog-preview-image]");
  const title = dialog.querySelector("[data-catalog-preview-title]");
  const category = dialog.querySelector("[data-catalog-preview-category]");
  const price = dialog.querySelector("[data-catalog-preview-price]");
  const rating = dialog.querySelector("[data-catalog-preview-rating]");
  const link = dialog.querySelector("[data-catalog-preview-link]");

  if (image instanceof HTMLImageElement) {
    image.src = trigger.dataset.previewImage || "";
    image.alt = trigger.dataset.previewTitle || "";
  }
  if (title instanceof HTMLElement) {
    title.textContent = trigger.dataset.previewTitle || "";
  }
  if (category instanceof HTMLElement) {
    category.textContent = trigger.dataset.previewCategory || "";
  }
  if (price instanceof HTMLElement) {
    price.textContent = trigger.dataset.previewPrice || "";
  }
  if (rating instanceof HTMLElement) {
    rating.textContent = trigger.dataset.previewRating || "";
  }
  if (link instanceof HTMLAnchorElement) {
    link.href = trigger.dataset.previewUrl || "#";
  }

  if (!dialog.open) {
    dialog.showModal();
  }

  requestAnimationFrame(() => {
    dialog.classList.remove("opacity-0", "scale-95");
    dialog.classList.add("opacity-100", "scale-100");
  });
}

function initCatalogPreviewDialog() {
  const dialog = getCatalogPreviewDialog();
  if (!dialog || dialog.dataset.catalogPreviewBound === "true") {
    return;
  }

  dialog.dataset.catalogPreviewBound = "true";

  dialog.querySelectorAll("[data-catalog-preview-close]").forEach((button) => {
    button.addEventListener("click", () => {
      closeCatalogPreviewDialog(dialog);
    });
  });

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      closeCatalogPreviewDialog(dialog);
    }
  });

  dialog.addEventListener("close", () => {
    dialog.classList.remove("opacity-100", "scale-100");
    dialog.classList.add("opacity-0", "scale-95");
  });
}

function parseJsonScript(selector, root = document) {
  const script = root.querySelector(selector);
  if (!script) return null;

  try {
    return JSON.parse(script.textContent || "null");
  } catch {
    return null;
  }
}

function updateCatalogMobileViewButtons(root, view) {
  root.querySelectorAll("[data-catalog-mobile-view]").forEach((button) => {
    const isActive = button.getAttribute("data-catalog-mobile-view") === view;
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
    button.classList.toggle("opacity-100", isActive);
    button.classList.toggle("opacity-40", !isActive);
  });
}

function fillCatalogMobileProduct(node, product) {
  node.querySelectorAll("[data-product-link]").forEach((link) => {
    if (link instanceof HTMLAnchorElement) {
      link.href = product.url || "#";
    }
  });

  const image = node.querySelector("[data-product-image]");
  if (image instanceof HTMLImageElement) {
    image.src = product.image_url || "";
    image.alt = product.title || "";
  }

  const title = node.querySelector("[data-product-title]");
  if (title instanceof HTMLElement) {
    title.textContent = product.title || "";
  }

  const kind = node.querySelector("[data-product-kind]");
  if (kind instanceof HTMLElement) {
    kind.textContent = product.category || "";
  }

  node.querySelectorAll("[data-product-category]").forEach((categoryNode) => {
    if (categoryNode instanceof HTMLElement) {
      categoryNode.textContent = product.category || "";
    }
  });

  const price = node.querySelector("[data-product-price]");
  if (price instanceof HTMLElement) {
    price.textContent = product.price || "";
  }

  const rating = node.querySelector("[data-product-rating]");
  if (rating instanceof HTMLElement) {
    rating.textContent = product.rating || "";
  }

  const favoriteButton = node.querySelector("[data-catalog-favorite-toggle]");
  if (favoriteButton instanceof HTMLElement) {
    favoriteButton.dataset.favorited = product.is_favorited ? "true" : "false";
    setFavoriteState(favoriteButton, Boolean(product.is_favorited));
  }

  const previewButton = node.querySelector("[data-catalog-preview-open]");
  if (previewButton instanceof HTMLElement) {
    previewButton.dataset.previewTitle = product.title || "";
    previewButton.dataset.previewCategory = product.category || "";
    previewButton.dataset.previewPrice = product.price || "";
    previewButton.dataset.previewRating = product.rating || "";
    previewButton.dataset.previewImage = product.image_url || "";
    previewButton.dataset.previewUrl = product.url || "#";
  }

  const cartButton = node.querySelector("[data-catalog-cart-toggle]");
  if (cartButton instanceof HTMLElement) {
    cartButton.dataset.productId = String(product.id || "");
    cartButton.dataset.inCart = product.is_in_cart ? "true" : "false";
    cartButton.dataset.isPurchased = product.is_purchased ? "true" : "false";
    cartButton.dataset.catalogDownloadUrl = product.download_url || "";
    setCartState(cartButton, Boolean(product.is_in_cart));
  }
}

function initCatalogMobileListing(root = document) {
  root.querySelectorAll("[data-catalog-mobile-listing]").forEach((listing) => {
    const isFirstBind = listing.dataset.catalogMobileBound !== "true";

    const products = parseJsonScript("#catalog-mobile-products-data", listing) || [];
    const listWrap = listing.querySelector("[data-catalog-mobile-products]");
    const emptyState = listing.querySelector("[data-catalog-mobile-empty]");
    const gridTemplate = listing.querySelector("template[data-catalog-mobile-grid-template]");
    const listTemplate = listing.querySelector("template[data-catalog-mobile-list-template]");

    if (!(listWrap instanceof HTMLElement) || !(gridTemplate instanceof HTMLTemplateElement) || !(listTemplate instanceof HTMLTemplateElement)) {
      return;
    }

    let state = listing._catalogMobileState;
    if (!state) {
      state = {
        view: "grid",
        products: [],
      };

      try {
        const stored = localStorage.getItem("catalogMobileView");
        if (stored === "grid" || stored === "list") {
          state.view = stored;
        }
      } catch {
        // ignore
      }

      listing._catalogMobileState = state;
    }

    state.products = Array.isArray(products) ? products : [];

    const render = () => {
      listWrap.replaceChildren();
      listWrap.className = "";

      if (state.view === "grid") {
        listWrap.classList.add("mt-[18px]", "grid", "grid-cols-[repeat(2,170px)]", "justify-center", "gap-x-[16px]", "gap-y-[16px]");
      } else {
        listWrap.classList.add("mt-[18px]", "flex", "flex-col", "gap-[10px]");
      }

      if (!state.products.length) {
        if (emptyState instanceof HTMLElement) {
          emptyState.hidden = false;
        }
        return;
      }

      if (emptyState instanceof HTMLElement) {
        emptyState.hidden = true;
      }

      const template = state.view === "grid" ? gridTemplate : listTemplate;
      for (const product of state.products) {
        const node = template.content.firstElementChild.cloneNode(true);
        fillCatalogMobileProduct(node, product);
        listWrap.appendChild(node);
      }

      initCatalogProductCards(listWrap);
    };

    updateCatalogMobileViewButtons(listing, state.view);
    render();

    if (isFirstBind) {
      listing.dataset.catalogMobileBound = "true";

      listing.querySelectorAll("[data-catalog-mobile-view]").forEach((button) => {
        button.addEventListener("click", () => {
          const nextView = button.getAttribute("data-catalog-mobile-view");
          if (!nextView || nextView === state.view) {
            return;
          }

          state.view = nextView;
          try {
            localStorage.setItem("catalogMobileView", nextView);
          } catch {
            // ignore
          }

          updateCatalogMobileViewButtons(listing, state.view);
          render();
        });
      });

      listing.querySelectorAll("[data-dialog-open]").forEach((button) => {
        button.addEventListener("click", () => {
          window.setTimeout(() => {
            const dialogId = button.getAttribute("data-dialog-open");
            if (!dialogId) {
              return;
            }

            const dialog = document.getElementById(dialogId);
            const surface = dialog?.querySelector("[data-catalog-mobile-filter-surface]");
            if (surface instanceof HTMLElement) {
              surface.focus({ preventScroll: true });
            }

            if (document.activeElement instanceof HTMLElement && document.activeElement.matches("[data-float-input]")) {
              document.activeElement.blur();
            }
          }, 0);
        });
      });
    }

  });
}

function initCatalogProductCards(root = document) {
  root.querySelectorAll("[data-catalog-favorite-toggle]").forEach((button) => {
    if (button.dataset.favoriteBound === "true") {
      return;
    }

    button.dataset.favoriteBound = "true";
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const isFavorited = button.dataset.favorited === "true";
      setFavoriteState(button, !isFavorited);
    });
  });

  root.querySelectorAll("[data-catalog-cart-toggle]").forEach((button) => {
    if (button.dataset.cartBound === "true") {
      return;
    }

    button.dataset.cartBound = "true";
    setCartState(button, button.dataset.inCart === "true");
    button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();

      if (button.dataset.cartPending === "true") {
        return;
      }

      const productId = Number.parseInt(button.dataset.productId || "", 10);
      if (!Number.isInteger(productId) || productId <= 0) {
        return;
      }

      if (button.dataset.isPurchased === "true") {
        const downloadUrl = button.dataset.catalogDownloadUrl || "/account/?tab=orders";
        window.location.href = downloadUrl;
        return;
      }

      button.dataset.cartPending = "true";
      button.setAttribute("aria-busy", "true");

      try {
        const payload = await toggleCartOnServer(productId);
        if (payload.already_purchased) {
          button.dataset.isPurchased = "true";
          setCartState(button, false);
          window.location.href = button.dataset.catalogDownloadUrl || "/account/?tab=orders";
          return;
        }
        setCartState(button, Boolean(payload.in_cart));
      } catch (error) {
        console.error(error);
      } finally {
        button.dataset.cartPending = "false";
        button.removeAttribute("aria-busy");
      }
    });
  });

  root.querySelectorAll("[data-catalog-preview-open]").forEach((button) => {
    if (button.dataset.previewBound === "true") {
      return;
    }

    button.dataset.previewBound = "true";
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      openCatalogPreviewDialog(button);
    });
  });
}

function initCatalogUi(root = document) {
  initCatalogFloatInputs(root);
  initCatalogDropdowns(root);
  initCatalogSortControls(root);
  initCatalogFilterReset(root);
  initCatalogMobileFilterDialog(root);
  initCatalogMobileListing(root);
  initCatalogProductCards(root);
  initCatalogPreviewDialog();
}

let pendingPaginationScrollAnchor = null;

function scrollToAnchor(anchorId) {
  if (!anchorId) {
    return;
  }

  const target = document.getElementById(anchorId);
  if (!(target instanceof HTMLElement)) {
    return;
  }

  target.scrollIntoView({ behavior: "smooth", block: "start" });
}

document.addEventListener("click", (event) => {
  const link = event.target instanceof Element ? event.target.closest("[data-pagination-scroll-anchor]") : null;
  if (!(link instanceof HTMLElement)) {
    return;
  }

  pendingPaginationScrollAnchor = link.dataset.paginationScrollAnchor || null;
});

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => initCatalogUi(document));
} else {
  initCatalogUi(document);
}

document.documentElement.style.scrollBehavior = "smooth";

function scrollToLocationHash() {
  if (window.location.hash) {
    window.setTimeout(() => {
      scrollToAnchor(window.location.hash.slice(1));
    }, 0);
  }
}

if (document.readyState === "loading") {
  window.addEventListener("DOMContentLoaded", scrollToLocationHash, { once: true });
} else {
  scrollToLocationHash();
}

document.body.addEventListener("htmx:load", () => {
  initCatalogUi(document);
});

document.body.addEventListener("htmx:afterSwap", () => {
  if (!pendingPaginationScrollAnchor) {
    return;
  }

  const anchorId = pendingPaginationScrollAnchor;
  pendingPaginationScrollAnchor = null;

  window.requestAnimationFrame(() => {
    scrollToAnchor(anchorId);
  });
});
