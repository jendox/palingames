(() => {
  function $(selector, root = document) {
    return root.querySelector(selector);
  }

  function $all(selector, root = document) {
    return Array.from(root.querySelectorAll(selector));
  }

  function parseJsonScript(selector, root) {
    const script = $(selector, root);
    if (!script) return null;
    try {
      return JSON.parse(script.textContent || "null");
    } catch {
      return null;
    }
  }

  function initFavoritesViewToggle(root, state, rerender) {
    const buttons = $all("[data-account-favorites-view]", root);
    if (!buttons.length) return;

    const apply = () => {
      for (const btn of buttons) {
        const view = btn.getAttribute("data-account-favorites-view");
        const isActive = view === state.favoritesView;
        btn.setAttribute("aria-pressed", isActive ? "true" : "false");
        btn.classList.toggle("opacity-100", isActive);
        btn.classList.toggle("opacity-40", !isActive);
      }
    };

    apply();

    for (const btn of buttons) {
      btn.addEventListener("click", () => {
        const view = btn.getAttribute("data-account-favorites-view");
        if (!view || view === state.favoritesView) return;
        state.favoritesView = view;
        try {
          localStorage.setItem("accountFavoritesView", view);
        } catch {
          // ignore
        }
        apply();
        rerender();
      });
    }
  }

  function initAccountMobile() {
    const root = $("[data-account-mobile]");
    if (!root) return;

    const demoData = parseJsonScript("[data-account-demo-data]", root);
    const renderPagination = window.AccountPagination?.render;

    const state = {
      ordersPage: 1,
      ordersPerPage: 2,
      favoritesPage: 1,
      favoritesPerPage: 6,
      favoritesView: "grid",
    };

    try {
      const stored = localStorage.getItem("accountFavoritesView");
      if (stored === "grid" || stored === "list") state.favoritesView = stored;
    } catch {
      // ignore
    }

    const orders = Array.isArray(demoData?.orders) ? demoData.orders : null;
    const favorites = Array.isArray(demoData?.favorites) ? demoData.favorites : null;

    const ordersWrap = $("[data-account-orders]", root);
    const ordersList = $("[data-account-orders-list]", root);
    const ordersEmpty = $("[data-account-orders-empty]", root);
    const ordersPagination = $("[data-account-orders-pagination]", root);
    const orderTemplate = $("template[data-account-order-template]", root);
    const orderItemTemplate = $("template[data-account-order-item-template]", root);

    const favoritesWrap = $("[data-account-favorites]", root);
    const favoritesList = $("[data-account-favorites-list]", root);
    const favoritesEmpty = $("[data-account-favorites-empty]", root);
    const favoritesPagination = $("[data-account-favorites-pagination]", root);
    const favoriteTemplate = $("template[data-account-favorite-template]", root);

    const renderOrders = () => {
      if (!ordersWrap || !ordersList || !ordersEmpty || !ordersPagination) return;
      ordersList.replaceChildren();

      if (!orders?.length || !orderTemplate || !orderItemTemplate || typeof renderPagination !== "function") {
        ordersEmpty.hidden = false;
        ordersPagination.replaceChildren();
        return;
      }

      ordersEmpty.hidden = true;

      const totalPages = Math.max(1, Math.ceil(orders.length / state.ordersPerPage));
      state.ordersPage = Math.min(state.ordersPage, totalPages);
      const start = (state.ordersPage - 1) * state.ordersPerPage;
      const pageOrders = orders.slice(start, start + state.ordersPerPage);

      for (const order of pageOrders) {
        const node = orderTemplate.content.firstElementChild.cloneNode(true);
        const num = node.querySelector("[data-order-number]");
        const date = node.querySelector("[data-order-date]");
        const total = node.querySelector("[data-order-total]");
        const itemsWrap = node.querySelector("[data-order-items]");

        if (num) num.textContent = order.number ?? "";
        if (date) date.textContent = order.date ?? "";
        if (total) total.textContent = order.total ?? "";

        if (itemsWrap) {
          itemsWrap.replaceChildren();
          for (const it of order.items || []) {
            const itemNode = orderItemTemplate.content.firstElementChild.cloneNode(true);
            const title = itemNode.querySelector("[data-item-title]");
            const price = itemNode.querySelector("[data-item-price]");
            const img = itemNode.querySelector("[data-item-img]");

            if (title) title.textContent = it.title ?? "";
            if (price) price.textContent = it.price ?? "";
            if (img && it.img) img.setAttribute("src", it.img);

            itemsWrap.appendChild(itemNode);
          }
        }

        ordersList.appendChild(node);
      }

      renderPagination(ordersPagination, {
        page: state.ordersPage,
        totalPages,
        onChange: (p) => {
          state.ordersPage = p;
          renderOrders();
        },
      });
    };

    const renderFavorites = () => {
      if (!favoritesWrap || !favoritesList || !favoritesEmpty || !favoritesPagination) return;
      favoritesList.replaceChildren();

      favoritesList.classList.remove("grid", "grid-cols-2", "flex", "flex-col");
      favoritesList.classList.add("gap-[10px]");
      if (state.favoritesView === "grid") {
        favoritesList.classList.add("grid", "grid-cols-2");
      } else {
        favoritesList.classList.add("flex", "flex-col");
      }

      if (!favorites?.length || !favoriteTemplate || typeof renderPagination !== "function") {
        favoritesEmpty.hidden = false;
        favoritesPagination.replaceChildren();
        return;
      }

      favoritesEmpty.hidden = true;

      const totalPages = Math.max(1, Math.ceil(favorites.length / state.favoritesPerPage));
      state.favoritesPage = Math.min(state.favoritesPage, totalPages);
      const start = (state.favoritesPage - 1) * state.favoritesPerPage;
      const pageItems = favorites.slice(start, start + state.favoritesPerPage);

      for (const fav of pageItems) {
        const node = favoriteTemplate.content.firstElementChild.cloneNode(true);
        const title = node.querySelector("[data-fav-title]");
        const price = node.querySelector("[data-fav-price]");
        const img = node.querySelector("[data-fav-img]");

        if (title) title.textContent = fav.title ?? "";
        if (price) price.textContent = fav.price ?? "";
        if (img && fav.img) img.setAttribute("src", fav.img);

        favoritesList.appendChild(node);
      }

      renderPagination(favoritesPagination, {
        page: state.favoritesPage,
        totalPages,
        onChange: (p) => {
          state.favoritesPage = p;
          renderFavorites();
        },
      });
    };

    initFavoritesViewToggle(root, state, renderFavorites);

    renderOrders();
    renderFavorites();

    root.addEventListener("click", (e) => {
      const filterBtn = e.target.closest?.("[data-account-filter]");
      if (!filterBtn) return;
      e.preventDefault();
      const kind = filterBtn.getAttribute("data-account-filter");
      console.info("Account filter clicked:", kind);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAccountMobile);
  } else {
    initAccountMobile();
  }
})();
