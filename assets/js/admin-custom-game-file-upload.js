(function () {
  const configEl = document.getElementById("custom-game-file-direct-upload-config");
  if (!configEl) return;

  let config;
  try {
    config = JSON.parse(configEl.textContent);
  } catch (_error) {
    return;
  }

  const uploadInput = document.getElementById("id_upload");
  const requestInput = document.getElementById("id_request");
  const isActiveInput = document.getElementById("id_is_active");
  const statusEl = document.getElementById("custom-game-file-direct-upload-status");
  const form = document.getElementById("customgamefile_form");

  if (!uploadInput || !form) return;

  let uploading = false;

  function getCsrfToken() {
    const input = document.querySelector("input[name=csrfmiddlewaretoken]");
    if (input && input.value) return input.value;

    const cookies = document.cookie.split(";");
    for (const cookie of cookies) {
      const trimmed = cookie.trim();
      const separatorIndex = trimmed.indexOf("=");
      if (separatorIndex === -1) continue;
      const key = trimmed.slice(0, separatorIndex);
      const value = trimmed.slice(separatorIndex + 1);
      if (key === "csrftoken") return decodeURIComponent(value);
    }
    return "";
  }

  function setStatus(message, isError) {
    if (!statusEl) return;
    statusEl.textContent = message;
    statusEl.style.color = isError ? "#ba2121" : "#417690";
  }

  function getRequestId() {
    if (!requestInput || !requestInput.value) return null;
    const parsed = Number.parseInt(requestInput.value, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),
      },
      body: JSON.stringify(body),
    });

    let data = {};
    try {
      data = await response.json();
    } catch (_error) {
      data = {};
    }

    if (!response.ok) {
      const err = data.error || `HTTP ${response.status}`;
      throw new Error(typeof err === "string" ? err : JSON.stringify(err));
    }

    return data;
  }

  function putFile(url, file, headers, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", url);
      for (const [key, value] of Object.entries(headers || {})) {
        xhr.setRequestHeader(key, value);
      }
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && onProgress) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve();
          return;
        }
        reject(new Error(`S3 upload failed: HTTP ${xhr.status}`));
      };
      xhr.onerror = () => reject(new Error("S3 upload network error"));
      xhr.send(file);
    });
  }

  async function handleFile(file) {
    if (uploading) return;

    const requestId = getRequestId();
    if (!requestId) {
      setStatus("Сначала выберите заказ.", true);
      uploadInput.value = "";
      return;
    }

    if (file.size > config.maxBytes) {
      setStatus("Файл слишком большой.", true);
      uploadInput.value = "";
      return;
    }

    uploading = true;
    setStatus("Подготовка загрузки…", false);

    try {
      const contentType = file.type || "application/octet-stream";
      const presign = await postJson(config.presignUrl, {
        request_id: requestId,
        filename: file.name,
        content_type: contentType,
        size_bytes: file.size,
      });

      setStatus("Загрузка в S3: 0%", false);
      await putFile(
        presign.upload_url,
        file,
        presign.required_headers,
        (percent) => setStatus(`Загрузка в S3: ${percent}%`, false),
      );

      setStatus("Сохранение метаданных…", false);
      const finalizeBody = {
        intent_id: presign.intent_id,
        request_id: requestId,
        file_key: presign.file_key,
        original_filename: file.name,
        mime_type: contentType,
        size_bytes: file.size,
        is_active: isActiveInput ? isActiveInput.checked : true,
      };
      if (config.customGameFileId) {
        finalizeBody.custom_game_file_id = config.customGameFileId;
      }

      const result = await postJson(config.finalizeUrl, finalizeBody);
      setStatus("Готово. Перенаправление…", false);
      window.location.href = result.redirect_url;
    } catch (error) {
      setStatus(error.message || "Ошибка загрузки.", true);
      uploadInput.value = "";
      uploading = false;
    }
  }

  uploadInput.addEventListener("change", () => {
    const file = uploadInput.files && uploadInput.files[0];
    if (!file) return;
    void handleFile(file);
  });

  form.addEventListener("submit", (event) => {
    if (uploading) {
      event.preventDefault();
      return;
    }
    if (uploadInput.files && uploadInput.files.length > 0) {
      event.preventDefault();
      setStatus("Дождитесь завершения прямой загрузки в S3 или очистите поле файла.", true);
    }
  });
})();
