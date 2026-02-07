(() => {
  function getDefaultVariant(container) {
    const fromAttr = container?.getAttribute?.("data-pagination-variant");
    if (fromAttr === "mobile" || fromAttr === "desktop") return fromAttr;
    return window.matchMedia?.("(min-width: 1024px)")?.matches ? "desktop" : "mobile";
  }

  function buildPageItems({ page, totalPages, delta = 1 }) {
    const safeTotal = Math.max(1, Number(totalPages) || 1);
    const safePage = Math.min(Math.max(1, Number(page) || 1), safeTotal);

    if (safeTotal <= 1) return { page: safePage, totalPages: safeTotal, items: [] };

    const visible = [];
    for (let i = 1; i <= safeTotal; i += 1) {
      if (i === 1 || i === safeTotal || (i >= safePage - delta && i <= safePage + delta)) {
        visible.push(i);
      }
    }

    const items = [];
    let last = 0;
    for (const p of visible) {
      if (p - last > 1) items.push({ type: "dots" });
      items.push({ type: "page", page: p, current: p === safePage });
      last = p;
    }

    return { page: safePage, totalPages: safeTotal, items };
  }

  const ICONS = {
    prev: "/static/images/icons/arrow-left.svg",
    next: "/static/images/icons/arrow-right.svg",
    pageActive: "/static/images/icons/page-active.svg",
    pageInactive: "/static/images/icons/page-inactive.svg",
    dots: "/static/images/icons/page-dots.svg",
  };

  function renderMobile(container, { page, totalPages, items, onChange }) {
    container.classList.add("account-pagination", "account-pagination--mobile");
    container.classList.remove("account-pagination--desktop");
    container.replaceChildren();

    if (totalPages <= 1) return;

    const mkArrow = ({ direction, targetPage, disabled }) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.disabled = disabled;
      btn.className = "account-pagination-arrow";
      btn.setAttribute("aria-label", direction === "prev" ? "Назад" : "Вперёд");

      const img = document.createElement("img");
      img.src = direction === "prev" ? ICONS.prev : ICONS.next;
      img.alt = "";
      img.setAttribute("aria-hidden", "true");
      img.setAttribute("draggable", "false");

      btn.appendChild(img);
      if (!disabled) btn.addEventListener("click", () => onChange(targetPage));
      return btn;
    };

    const mkPage = ({ pageNumber, current }) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "account-pagination-page";
      if (current) {
        btn.disabled = true;
        btn.setAttribute("aria-current", "page");
      } else {
        btn.addEventListener("click", () => onChange(pageNumber));
      }

      const bg = document.createElement("img");
      bg.src = current ? ICONS.pageActive : ICONS.pageInactive;
      bg.alt = "";
      bg.className = "account-pagination-bg";
      bg.setAttribute("aria-hidden", "true");
      bg.setAttribute("draggable", "false");

      const num = document.createElement("span");
      num.className = "account-pagination-number";
      num.textContent = String(pageNumber);

      btn.append(bg, num);
      return btn;
    };

    const mkDots = () => {
      const wrap = document.createElement("span");
      wrap.className = "account-pagination-dots";
      wrap.setAttribute("aria-hidden", "true");

      const img = document.createElement("img");
      img.src = ICONS.dots;
      img.alt = "";
      img.setAttribute("aria-hidden", "true");
      img.setAttribute("draggable", "false");

      wrap.appendChild(img);
      return wrap;
    };

    container.appendChild(
      mkArrow({
        direction: "prev",
        targetPage: Math.max(1, page - 1),
        disabled: page <= 1,
      }),
    );

    for (const it of items) {
      if (it.type === "dots") container.appendChild(mkDots());
      if (it.type === "page") container.appendChild(mkPage({ pageNumber: it.page, current: it.current }));
    }

    container.appendChild(
      mkArrow({
        direction: "next",
        targetPage: Math.min(totalPages, page + 1),
        disabled: page >= totalPages,
      }),
    );
  }

  function renderDesktop(container, { page, totalPages, items, onChange }) {
    container.classList.add("account-pagination", "account-pagination--desktop");
    container.classList.remove("account-pagination--mobile");
    container.replaceChildren();

    if (totalPages <= 1) return;

    const mkNavBtn = ({ direction, targetPage, disabled }) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.disabled = disabled;
      btn.className = `account-pagination-nav-btn account-pagination-nav-btn-${direction}`;
      btn.setAttribute("aria-label", direction === "prev" ? "Предыдущая" : "Следующая");

      const label = document.createElement("span");
      label.className = "account-pagination-nav-text";
      label.textContent = direction === "prev" ? "предыдущая" : "следующая";

      const icon = document.createElement("img");
      icon.src = direction === "prev" ? ICONS.next : ICONS.prev;
      icon.alt = "";
      icon.className = "account-pagination-nav-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.setAttribute("draggable", "false");

      if (direction === "prev") {
        btn.append(label, icon);
      } else {
        btn.append(icon, label);
      }

      if (!disabled) btn.addEventListener("click", () => onChange(targetPage));
      return btn;
    };

    const pagesWrap = document.createElement("div");
    pagesWrap.className = "account-pagination-pages";

    const mkPage = ({ pageNumber, current }) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `account-pagination-page account-pagination-page--desktop${current ? " is-current" : ""}`;

      if (current) {
        btn.disabled = true;
        btn.setAttribute("aria-current", "page");
      } else {
        btn.addEventListener("click", () => onChange(pageNumber));
      }

      const num = document.createElement("span");
      num.className = "account-pagination-number";
      num.textContent = String(pageNumber);

      btn.appendChild(num);
      return btn;
    };

    const mkDots = () => {
      const wrap = document.createElement("span");
      wrap.className = "account-pagination-dots account-pagination-dots--desktop";
      wrap.setAttribute("aria-hidden", "true");

      const img = document.createElement("img");
      img.src = ICONS.dots;
      img.alt = "";
      img.setAttribute("aria-hidden", "true");
      img.setAttribute("draggable", "false");

      wrap.appendChild(img);
      return wrap;
    };

    for (const it of items) {
      if (it.type === "dots") pagesWrap.appendChild(mkDots());
      if (it.type === "page") pagesWrap.appendChild(mkPage({ pageNumber: it.page, current: it.current }));
    }

    container.appendChild(
      mkNavBtn({
        direction: "prev",
        targetPage: Math.max(1, page - 1),
        disabled: page <= 1,
      }),
    );
    container.appendChild(pagesWrap);
    container.appendChild(
      mkNavBtn({
        direction: "next",
        targetPage: Math.min(totalPages, page + 1),
        disabled: page >= totalPages,
      }),
    );
  }

  function render(container, { page, totalPages, onChange, variant } = {}) {
    if (!container) return;
    if (typeof onChange !== "function") return;

    const chosenVariant = variant || getDefaultVariant(container);
    const model = buildPageItems({ page, totalPages, delta: 1 });

    if (chosenVariant === "desktop") {
      renderDesktop(container, { ...model, onChange });
    } else {
      renderMobile(container, { ...model, onChange });
    }
  }

  window.AccountPagination = {
    render,
  };
})();
