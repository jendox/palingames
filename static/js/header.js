(function () {
  // Temporary restriction: keep the secondary desktop header sticky only on the home page.
  // To restore the old behavior everywhere, remove this guard.
  if (document.body?.dataset.pageName !== "home") return;

  const secondaryWrap = document.querySelector("[data-header-secondary-wrap]");
  const secondaryNav = document.querySelector("[data-header-secondary-nav]");
  if (!secondaryWrap || !secondaryNav) return;

  const iconEls = Array.from(secondaryNav.querySelectorAll("[data-header-nav-icon]"));
  const labelEls = Array.from(secondaryNav.querySelectorAll("[data-header-nav-label]"));

  const fixedNavClasses = [
    "fixed",
    "left-0",
    "right-0",
    "top-0",
    "z-50",
    "pt-[20px]",
    "pb-[20px]",
    "bg-[rgba(251,251,251,0.6)]",
    "backdrop-blur-sm",
  ];
  const fixedPaddingAdd = ["min-[1920px]:pl-[326px]", "min-[1920px]:pr-[326px]"];
  const fixedPaddingRemove = ["min-[1920px]:pl-[146px]", "min-[1920px]:pr-[176px]"];

  function setIconsVisible(visible) {
    for (const el of iconEls) {
      // Иконки должны исчезать сразу при возврате наверх (без плавного transition).
      // При скролле вниз появление оставляем анимированным.
      if (visible) {
        el.style.transitionDuration = "";
      } else {
        el.style.transitionDuration = "0ms";
      }

      el.classList.toggle("max-h-0", !visible);
      el.classList.toggle("opacity-0", !visible);
      el.classList.toggle("-translate-y-2", !visible);

      el.classList.toggle("max-h-[74px]", visible);
      el.classList.toggle("opacity-100", visible);
      el.classList.toggle("translate-y-0", visible);
    }

    for (const el of labelEls) {
      // Текст (отступ сверху) тоже должен "схлопываться" мгновенно при возврате наверх.
      if (visible) {
        el.style.transitionDuration = "";
      } else {
        el.style.transitionDuration = "0ms";
      }

      el.classList.toggle("mt-0", !visible);
      el.classList.toggle("mt-[20px]", visible);
    }
  }

  function setFixed(fixed) {
    if (fixed) {
      secondaryWrap.classList.remove("pt-[76px]", "pb-14");
      secondaryWrap.classList.add("pt-0", "pb-0");

      for (const cls of fixedPaddingRemove) secondaryNav.classList.remove(cls);
      for (const cls of fixedPaddingAdd) secondaryNav.classList.add(cls);

      for (const cls of fixedNavClasses) secondaryNav.classList.add(cls);

      secondaryWrap.style.height = `${secondaryNav.scrollHeight}px`;
      return;
    }

    for (const cls of fixedNavClasses) secondaryNav.classList.remove(cls);

    for (const cls of fixedPaddingAdd) secondaryNav.classList.remove(cls);
    for (const cls of fixedPaddingRemove) secondaryNav.classList.add(cls);

    secondaryWrap.classList.remove("pt-0", "pb-0");
    secondaryWrap.classList.add("pt-[76px]", "pb-14");
    secondaryWrap.style.height = "";
  }

  let lastScrolled = null;
  function update() {
    const scrolled = window.scrollY > 0;
    if (scrolled === lastScrolled) return;
    lastScrolled = scrolled;

    setIconsVisible(scrolled);
    setFixed(scrolled);
  }

  let scheduled = false;
  function scheduleUpdate() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      update();
    });
  }

  window.addEventListener("scroll", scheduleUpdate, { passive: true });
  window.addEventListener(
    "resize",
    () => {
      if (lastScrolled) secondaryWrap.style.height = `${secondaryNav.scrollHeight}px`;
    },
    { passive: true },
  );
  update();
})();

(function () {
  const dropdownEls = Array.from(document.querySelectorAll("[data-account-dropdown]"));
  if (!dropdownEls.length) return;

  function syncAria(detailsEl) {
    const summaryEl = detailsEl.querySelector("summary");
    if (!summaryEl) return;
    summaryEl.setAttribute("aria-expanded", detailsEl.open ? "true" : "false");
  }

  function closeDropdown(detailsEl, { focusButton } = { focusButton: false }) {
    if (!detailsEl.open) return;
    detailsEl.open = false;
    syncAria(detailsEl);
    if (focusButton) detailsEl.querySelector("summary")?.focus();
  }

  function closeOtherDropdowns(openDetailsEl) {
    for (const detailsEl of dropdownEls) {
      if (detailsEl !== openDetailsEl) closeDropdown(detailsEl);
    }
  }

  for (const detailsEl of dropdownEls) {
    const summaryEl = detailsEl.querySelector("summary");
    const menuEl = detailsEl.querySelector("[data-account-dropdown-menu]");
    if (!summaryEl || !menuEl) continue;

    syncAria(detailsEl);

    detailsEl.addEventListener("toggle", () => {
      syncAria(detailsEl);
      if (detailsEl.open) closeOtherDropdowns(detailsEl);
    });

    detailsEl.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (detailsEl.open) {
        event.preventDefault();
        event.stopPropagation();
      }
      closeDropdown(detailsEl, { focusButton: true });
    });

    detailsEl.addEventListener("focusout", (event) => {
      if (!detailsEl.open) return;

      const nextFocused = event.relatedTarget;
      if (nextFocused instanceof Node && detailsEl.contains(nextFocused)) return;

      setTimeout(() => {
        if (!detailsEl.open) return;
        const active = document.activeElement;
        if (active instanceof Node && detailsEl.contains(active)) return;
        closeDropdown(detailsEl);
      }, 0);
    });

    menuEl.addEventListener("click", (event) => {
      const clickedItem = event.target?.closest?.("a,button");
      if (!clickedItem) return;
      closeDropdown(detailsEl);
    });
  }

  document.addEventListener(
    "pointerdown",
    (event) => {
      const target = event.target;
      for (const detailsEl of dropdownEls) {
        if (!detailsEl.open) continue;
        if (target instanceof Node && detailsEl.contains(target)) continue;
        closeDropdown(detailsEl);
      }
    },
    { capture: true },
  );
})();

(function () {
  const searchToggle = document.querySelector("[data-mobile-search-toggle]");
  const searchPanel = document.querySelector("[data-mobile-search-panel]");
  const searchClose = document.querySelector("[data-mobile-search-close]");
  const menuToggle = document.querySelector("[data-mobile-menu-toggle]");
  const menuEl = document.querySelector("[data-mobile-menu]");

  if (!menuToggle || !menuEl) return;

  function setMenuOpen(open) {
    menuToggle.setAttribute("aria-expanded", open ? "true" : "false");
    menuEl.classList.toggle("hidden", !open);
  }

  function isMenuOpen() {
    return menuToggle.getAttribute("aria-expanded") === "true";
  }

  function isSearchOpen() {
    return Boolean(searchPanel && !searchPanel.hidden);
  }

  function setSearchOpen(open) {
    if (!searchPanel || !searchToggle) return;
    searchToggle.setAttribute("aria-expanded", open ? "true" : "false");
    searchPanel.hidden = !open;
    if (!open) {
      const si = document.querySelector("[data-mobile-search-input]");
      const su = document.querySelector("[data-mobile-search-suggestions]");
      if (si) si.value = "";
      if (su) su.replaceChildren();
    } else {
      const si = document.querySelector("[data-mobile-search-input]");
      window.requestAnimationFrame(() => si?.focus());
    }
  }

  searchToggle?.addEventListener("click", () => {
    if (isMenuOpen()) setMenuOpen(false);
    setSearchOpen(!isSearchOpen());
  });

  searchClose?.addEventListener("click", () => {
    setSearchOpen(false);
    searchToggle?.focus();
  });

  menuToggle.addEventListener("click", () => {
    if (isSearchOpen()) setSearchOpen(false);
    setMenuOpen(!isMenuOpen());
  });

  menuEl.addEventListener("click", (event) => {
    const link = event.target?.closest?.("a");
    if (!link) return;
    setMenuOpen(false);
  });

  document.addEventListener(
    "pointerdown",
    (event) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (isMenuOpen()) {
        if (menuToggle.contains(target) || menuEl.contains(target)) return;
        setMenuOpen(false);
      }
      if (isSearchOpen() && searchPanel && searchToggle) {
        if (searchToggle.contains(target) || searchPanel.contains(target)) return;
        setSearchOpen(false);
      }
    },
    { capture: true },
  );

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (isSearchOpen()) {
      event.preventDefault();
      setSearchOpen(false);
      searchToggle?.focus();
      return;
    }
    if (!isMenuOpen()) return;
    event.preventDefault();
    setMenuOpen(false);
    menuToggle.focus();
  });

  setMenuOpen(false);
  setSearchOpen(false);
})();

(function () {
  const suggestUrl = document.body?.dataset.catalogSuggestUrl;
  if (!suggestUrl) return;

  const debounceMs = 280;
  let desktopTimer = null;
  let mobileTimer = null;

  function debouncedFetch(inputEl, listEl, abortRef, minChars) {
    const q = (inputEl.value || "").trim();
    if (q.length < minChars) {
      listEl.hidden = true;
      listEl.classList.add("invisible", "opacity-0", "pointer-events-none");
      listEl.replaceChildren();
      return;
    }

    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    fetch(`${suggestUrl}?q=${encodeURIComponent(q)}`, {
      signal: controller.signal,
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((data) => {
        const results = Array.isArray(data?.results) ? data.results : [];
        listEl.replaceChildren();
        for (const item of results) {
          if (!item?.title || !item?.url) continue;
          const li = document.createElement("li");
          li.className = "m-0 list-none";
          const a = document.createElement("a");
          a.href = item.url;
          a.textContent = item.title;
          a.className = "block px-4 py-2 hover:bg-[var(--color-lilac)]/40";
          li.appendChild(a);
          listEl.appendChild(li);
        }
        const has = listEl.children.length > 0;
        listEl.hidden = !has;
        if (listEl.id === "header-search-suggestions") {
          listEl.classList.toggle("invisible", !has);
          listEl.classList.toggle("opacity-0", !has);
          listEl.classList.toggle("pointer-events-none", !has);
        }
      })
      .catch(() => {
        listEl.replaceChildren();
        listEl.hidden = true;
      });
  }

  const desktopInput = document.querySelector("[data-header-search-input]");
  const desktopList = document.querySelector("[data-header-search-suggestions]");
  const mobileInput = document.querySelector("[data-mobile-search-input]");
  const mobileList = document.querySelector("[data-mobile-search-suggestions]");

  if (
    (window.location.pathname === "/catalog" || window.location.pathname === "/catalog/") &&
    window.location.search
  ) {
    const q = new URLSearchParams(window.location.search).get("q");
    if (q) {
      if (desktopInput) desktopInput.value = q;
      if (mobileInput) mobileInput.value = q;
    }
  }

  if (desktopInput && desktopList) {
    const abortRef = { current: null };
    desktopInput.addEventListener("input", () => {
      window.clearTimeout(desktopTimer);
      desktopTimer = window.setTimeout(() => {
        debouncedFetch(desktopInput, desktopList, abortRef, 2);
      }, debounceMs);
    });
    desktopInput.addEventListener("blur", () => {
      window.setTimeout(() => {
        desktopList.hidden = true;
        desktopList.classList.add("invisible", "opacity-0", "pointer-events-none");
      }, 200);
    });
    desktopInput.addEventListener("focus", () => {
      if (desktopList.children.length > 0) {
        desktopList.hidden = false;
        desktopList.classList.remove("invisible", "opacity-0", "pointer-events-none");
      }
    });
  }

  if (mobileInput && mobileList) {
    const abortRef = { current: null };
    mobileInput.addEventListener("input", () => {
      window.clearTimeout(mobileTimer);
      mobileTimer = window.setTimeout(() => {
        debouncedFetch(mobileInput, mobileList, abortRef, 2);
      }, debounceMs);
    });
  }
})();
