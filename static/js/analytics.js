(function () {
  const dataLayer = (window.dataLayer = window.dataLayer || []);
  const body = document.body;

  if (!body) {
    return;
  }

  function getPageType() {
    const pageName = body.dataset.pageName || "";
    const mapping = {
      home: "home",
      catalog: "catalog",
      product: "product",
      cart: "cart",
      checkout: "checkout",
      account: "account",
      "custom-game": "custom_game",
      about: "about",
      favorites: "favorites",
      payment: "payment",
      "alphabet-navigator": "alphabet_navigator",
    };

    return mapping[pageName] || pageName || "unknown";
  }

  function normalizePayload(payload) {
    return Object.entries(payload).reduce((result, [key, value]) => {
      if (value === undefined || value === null || value === "") {
        return result;
      }
      result[key] = value;
      return result;
    }, {});
  }

  function parseJsonScript(scriptId) {
    const node = document.getElementById(scriptId);
    if (!node) {
      return null;
    }

    try {
      return JSON.parse(node.textContent || "null");
    } catch (_error) {
      return null;
    }
  }

  function parseAnalyticsItemPrice(value) {
    const parsed = Number.parseFloat(value || "");
    return Number.isFinite(parsed) ? parsed : undefined;
  }

  function extractItemFromElement(element) {
    if (!(element instanceof HTMLElement)) {
      return null;
    }

    const itemId = element.dataset.analyticsItemId || "";
    const itemName = element.dataset.analyticsItemName || "";
    if (!itemId || !itemName) {
      return null;
    }

    return normalizePayload({
      item_id: itemId,
      item_name: itemName,
      item_category: element.dataset.analyticsItemCategory || "",
      item_variant: element.dataset.analyticsItemVariant || "",
      price: parseAnalyticsItemPrice(element.dataset.analyticsPrice),
      currency: element.dataset.analyticsCurrency || "",
    });
  }

  function trackEvent(name, payload = {}) {
    if (!name) {
      return;
    }

    dataLayer.push(
      normalizePayload({
        event: name,
        ...payload,
      }),
    );
  }

  function trackViewItemList(payload) {
    if (!Array.isArray(payload?.items) || !payload.items.length) {
      return;
    }

    trackEvent("view_item_list", {
      ecommerce: {
        item_list_name: payload.item_list_name,
        items: payload.items,
      },
    });
  }

  function trackViewItem(item) {
    if (!item?.item_id || !item?.item_name) {
      return;
    }

    trackEvent("view_item", {
      ecommerce: {
        currency: item.currency,
        value: item.price,
        items: [item],
      },
    });
  }

  function trackAddToCart(item) {
    if (!item?.item_id || !item?.item_name) {
      return;
    }

    trackEvent("add_to_cart", {
      ecommerce: {
        currency: item.currency,
        value: item.price,
        items: [
          {
            ...item,
            quantity: 1,
          },
        ],
      },
    });
  }

  function trackAddToCartFromElement(element) {
    const item = extractItemFromElement(element);
    if (!item) {
      return;
    }
    trackAddToCart(item);
  }

  function trackBeginCheckout(payload) {
    if (!Array.isArray(payload?.items) || !payload.items.length) {
      return;
    }

    trackEvent("begin_checkout", {
      ecommerce: {
        currency: payload.currency,
        value: payload.value,
        items: payload.items,
      },
    });
  }

  function collectVisibleListItems() {
    const elements = Array.from(document.querySelectorAll("[data-analytics-item]")).filter(
      (element) => element instanceof HTMLElement && element.offsetParent !== null,
    );

    const itemsById = new Map();
    for (const element of elements) {
      const item = extractItemFromElement(element);
      if (!item?.item_id || itemsById.has(item.item_id)) {
        continue;
      }
      itemsById.set(item.item_id, item);
    }

    return Array.from(itemsById.values());
  }

  function currentCatalogListName() {
    const searchQuery = new URLSearchParams(window.location.search).get("q");
    if (searchQuery) {
      return "search_results";
    }

    const category = new URLSearchParams(window.location.search).get("category");
    if (category) {
      return category;
    }

    return getPageType();
  }

  let lastTrackedListSignature = "";

  function maybeTrackCatalogViewList() {
    const pageType = getPageType();
    if (!["catalog", "alphabet_navigator", "favorites"].includes(pageType)) {
      return;
    }

    const items = collectVisibleListItems();
    if (!items.length) {
      return;
    }

    const signature = JSON.stringify({
      pageType,
      path: window.location.pathname + window.location.search,
      ids: items.map((item) => item.item_id),
    });
    if (signature === lastTrackedListSignature) {
      return;
    }
    lastTrackedListSignature = signature;

    trackViewItemList({
      item_list_name: currentCatalogListName(),
      items,
    });
  }

  function maybeTrackProductView() {
    if (getPageType() !== "product") {
      return;
    }

    const item = parseJsonScript("product-analytics-item");
    if (!item) {
      return;
    }

    trackViewItem(item);
  }

  function maybeTrackBeginCheckout() {
    if (getPageType() !== "checkout") {
      return;
    }

    const payload = parseJsonScript("checkout-analytics-payload");
    if (!payload) {
      return;
    }

    const signature = JSON.stringify(payload);
    if (body.dataset.analyticsBeginCheckoutTracked === signature) {
      return;
    }
    body.dataset.analyticsBeginCheckoutTracked = signature;

    trackBeginCheckout(payload);
  }

  function trackPageView() {
    trackEvent("page_view", {
      page_type: getPageType(),
      page_path: window.location.pathname + window.location.search,
      page_title: body.dataset.pageTitle || document.title,
      user_type: body.dataset.userType || "guest",
    });
  }

  function bootstrapPageAnalytics() {
    trackPageView();
    maybeTrackCatalogViewList();
    maybeTrackProductView();
    maybeTrackBeginCheckout();
  }

  window.PaliAnalytics = {
    trackAddToCart,
    trackAddToCartFromElement,
    trackBeginCheckout,
    trackEvent,
    trackViewItem,
    trackViewItemList,
  };

  document.addEventListener("DOMContentLoaded", bootstrapPageAnalytics);

  document.body.addEventListener("htmx:afterSwap", () => {
    window.requestAnimationFrame(() => {
      maybeTrackCatalogViewList();
      maybeTrackBeginCheckout();
    });
  });
})();
