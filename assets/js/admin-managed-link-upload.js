(function () {
  const configEl = document.getElementById("managed-link-direct-upload-config");
  if (!configEl) return;

  let config;
  try {
    config = JSON.parse(configEl.textContent);
  } catch (_error) {
    return;
  }

  const uploadInput = document.getElementById("managed-link-file-upload");
  const statusEl = document.getElementById("managed-link-direct-upload-status");

  if (!uploadInput) return;

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
        reject(new Error(`Upload failed with status ${xhr.status}`));
      };
      xhr.onerror = () => reject(new Error("Upload network error"));
      xhr.send(file);
    });
  }

  uploadInput.addEventListener("change", async () => {
    const file = uploadInput.files && uploadInput.files[0];
    if (!file || uploading) return;

    uploading = true;
    uploadInput.disabled = true;
    setStatus("Подготовка загрузки...", false);

    try {
      const presign = await postJson(config.presignUrl, {
        managed_link_id: config.managedLinkId,
        filename: file.name,
        content_type: file.type || "application/octet-stream",
        size_bytes: file.size,
      });

      setStatus("Загрузка в S3...", false);
      await putFile(
        presign.upload_url,
        file,
        presign.required_headers,
        (percent) => setStatus(`Загрузка в S3: ${percent}%`, false),
      );

      setStatus("Подтверждение загрузки...", false);
      const finalize = await postJson(config.finalizeUrl, {
        intent_id: presign.intent_id,
        managed_link_id: config.managedLinkId,
        file_key: presign.file_key,
        original_filename: file.name,
        mime_type: file.type || presign.required_headers["Content-Type"],
        size_bytes: file.size,
      });

      setStatus("Файл загружен. Обновление страницы...", false);
      window.location.href = finalize.redirect_url;
    } catch (error) {
      setStatus(error.message || "Ошибка загрузки.", true);
      uploading = false;
      uploadInput.disabled = false;
    }
  });
})();
