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
