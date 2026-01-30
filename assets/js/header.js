(function () {
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
      el.classList.toggle("max-h-0", !visible);
      el.classList.toggle("opacity-0", !visible);
      el.classList.toggle("-translate-y-2", !visible);

      el.classList.toggle("max-h-[74px]", visible);
      el.classList.toggle("opacity-100", visible);
      el.classList.toggle("translate-y-0", visible);
    }

    for (const el of labelEls) {
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
  const toggleBtn = document.querySelector("[data-mobile-menu-toggle]");
  const menuEl = document.querySelector("[data-mobile-menu]");
  if (!toggleBtn || !menuEl) return;

  function setOpen(open) {
    toggleBtn.setAttribute("aria-expanded", open ? "true" : "false");
    menuEl.classList.toggle("hidden", !open);
  }

  function isOpen() {
    return toggleBtn.getAttribute("aria-expanded") === "true";
  }

  toggleBtn.addEventListener("click", () => {
    setOpen(!isOpen());
  });

  menuEl.addEventListener("click", (event) => {
    const link = event.target?.closest?.("a");
    if (!link) return;
    setOpen(false);
  });

  document.addEventListener(
    "pointerdown",
    (event) => {
      if (!isOpen()) return;
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (toggleBtn.contains(target) || menuEl.contains(target)) return;
      setOpen(false);
    },
    { capture: true },
  );

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!isOpen()) return;
    event.preventDefault();
    setOpen(false);
    toggleBtn.focus();
  });

  setOpen(false);
})();
