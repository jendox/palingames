(function () {
  let lastOpener = null;

  function getDialogById(id) {
    const dlg = document.getElementById(id);
    return dlg && dlg.tagName === "DIALOG" ? dlg : null;
  }

  function openDialog(dlg, opener) {
    if (!dlg) return;
    lastOpener = opener || null;

    if (!dlg.open) dlg.showModal();

    // следующий тик, чтобы transition отработал
    requestAnimationFrame(() => {
      dlg.classList.remove("opacity-0", "scale-95");
      dlg.classList.add("opacity-100", "scale-100");
    });
  }

  function closeDialog(dlg) {
    if (!dlg) return;

    // запускаем анимацию закрытия
    dlg.classList.remove("opacity-100", "scale-100");
    dlg.classList.add("opacity-0", "scale-95");

    // ждём окончания transition и закрываем
    setTimeout(() => {
      if (dlg.open) dlg.close();
    }, 160);
  }

  document.addEventListener("click", (e) => {
    const switchBtn = e.target.closest("[data-dialog-switch]");
    if (switchBtn) {
      e.preventDefault();

      const id = switchBtn.getAttribute("data-dialog-switch");
      const fromDlg = switchBtn.closest("dialog");

      if (fromDlg && fromDlg.open) {
        closeDialog(fromDlg);
        setTimeout(() => {
          openDialog(getDialogById(id), switchBtn);
        }, 170);
        return;
      }

      openDialog(getDialogById(id), switchBtn);
      return;
    }

    const openBtn = e.target.closest("[data-dialog-open]");
    if (openBtn) {
      e.preventDefault();
      const id = openBtn.getAttribute("data-dialog-open");
      openDialog(getDialogById(id), openBtn);
      return;
    }

    const closeBtn = e.target.closest("[data-dialog-close]");
    if (closeBtn) {
      const dlg = closeBtn.closest("dialog");
      closeDialog(dlg);
      return;
    }

    // Закрытие по клику на backdrop:
    // у <dialog> клик по "серому" фону приходит как click по самому dialog
    const dlg = e.target.closest("dialog");
    if (dlg && dlg.open && e.target === dlg) {
      closeDialog(dlg);
    }
  });

  // Возврат фокуса на кнопку, которая открыла модалку
  document.addEventListener("close", (e) => {
    const dlg = e.target;
    if (dlg && dlg.tagName === "DIALOG" && lastOpener) {
      lastOpener.focus();
      lastOpener = null;
    }
  }, true);
})();
