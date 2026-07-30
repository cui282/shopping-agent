import { useEffect, useId, useRef, useState } from "react";
import { ImagePlus, LoaderCircle, Send, Square, Trash2 } from "lucide-react";
import { api } from "../api/client";
import type { UploadResponse } from "../types/api";
import { prepareShoppingQuery, queryCharacterCount, QUERY_MAX_LENGTH } from "../utils/queryContract";
import styles from "./QueryComposer.module.css";

interface UploadedImage {
  uploadId: string;
  name: string;
  previewUrl: string;
}

interface QueryComposerProps {
  value: string;
  busy: boolean;
  canCancel: boolean;
  disabledReason: string | null;
  allowImageUpload: boolean;
  attachmentResetKey: number;
  onChange: (value: string) => void;
  onSubmit: (uploadIds: string[]) => void;
  onCancel: () => void;
}

export default function QueryComposer({
  value,
  busy,
  canCancel,
  disabledReason,
  allowImageUpload,
  attachmentResetKey,
  onChange,
  onSubmit,
  onCancel,
}: QueryComposerProps) {
  const [image, setImage] = useState<UploadedImage | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const uploadRequestRef = useRef<AbortController | null>(null);
  const errorId = useId();

  useEffect(() => {
    const area = textareaRef.current;
    if (!area) return;
    area.style.height = "0px";
    area.style.height = `${Math.min(Math.max(area.scrollHeight, 52), 120)}px`;
  }, [value]);

  useEffect(
    () => () => {
      uploadRequestRef.current?.abort();
      if (image) URL.revokeObjectURL(image.previewUrl);
    },
    [image],
  );

  useEffect(() => {
    uploadRequestRef.current?.abort();
    uploadRequestRef.current = null;
    setImage((current) => {
      if (current) URL.revokeObjectURL(current.previewUrl);
      return null;
    });
  }, [attachmentResetKey]);

  useEffect(() => {
    if (allowImageUpload) return;
    uploadRequestRef.current?.abort();
    uploadRequestRef.current = null;
    setUploading(false);
    setImage((current) => {
      if (current) URL.revokeObjectURL(current.previewUrl);
      return null;
    });
  }, [allowImageUpload]);

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      setError("请选择 JPG、PNG 或 WebP 图片");
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      setError("图片不能超过 8 MiB");
      return;
    }
    setUploading(true);
    uploadRequestRef.current?.abort();
    const controller = new AbortController();
    uploadRequestRef.current = controller;
    try {
      const uploaded: UploadResponse = await api.upload(file, { signal: controller.signal });
      if (uploadRequestRef.current !== controller) return;
      if (image) URL.revokeObjectURL(image.previewUrl);
      setImage({ uploadId: uploaded.upload_id, name: uploaded.name ?? uploaded.filename ?? file.name, previewUrl: URL.createObjectURL(file) });
    } catch (uploadError) {
      if (uploadError instanceof DOMException && uploadError.name === "AbortError") return;
      setError(uploadError instanceof Error ? uploadError.message : "图片上传失败");
    } finally {
      if (uploadRequestRef.current === controller) {
        uploadRequestRef.current = null;
        setUploading(false);
        if (inputRef.current) inputRef.current.value = "";
      }
    }
  };

  const submit = () => {
    const prepared = prepareShoppingQuery(value);
    if (prepared.error) {
      setError(prepared.error);
      textareaRef.current?.focus();
      return;
    }
    setError(null);
    onSubmit(image ? [image.uploadId] : []);
  };

  return (
    <div className={styles.composer}>
      {image && (
        <div className={styles.attachment}>
          <img src={image.previewUrl} width="44" height="44" alt="上传的商品参考图" />
          <span>{image.name}</span>
          <button
            type="button"
            className={styles.remove}
            onClick={() => {
              URL.revokeObjectURL(image.previewUrl);
              setImage(null);
            }}
            aria-label="移除参考图"
            title="移除参考图"
          >
            <Trash2 size={15} aria-hidden="true" />
          </button>
        </div>
      )}
      <div className={styles.inputRow} data-error={Boolean(error)}>
        <label className={styles.visuallyHidden} htmlFor={errorId}>
          购物需求
        </label>
        <textarea
          id={errorId}
          name="shopping-query"
          ref={textareaRef}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              if (!busy && !uploading && !disabledReason) submit();
            }
          }}
          placeholder="商品、预算、用途与条件"
          aria-describedby={error || disabledReason ? `${errorId}-message` : undefined}
          aria-invalid={Boolean(error)}
          disabled={busy}
        />
        <div className={styles.actions}>
          {allowImageUpload && (
            <>
              <input
                ref={inputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                className={styles.fileInput}
                onChange={(event) => void handleFile(event.target.files?.[0])}
              />
              <button
                type="button"
                className={styles.iconButton}
                onClick={() => inputRef.current?.click()}
                disabled={busy || uploading}
                aria-label="上传商品参考图"
                title="上传商品参考图"
              >
                {uploading ? (
                  <LoaderCircle className={styles.spinning} size={18} aria-hidden="true" />
                ) : (
                  <ImagePlus size={18} aria-hidden="true" />
                )}
              </button>
            </>
          )}
          {busy && canCancel ? (
            <button type="button" className={styles.cancelButton} onClick={onCancel} aria-label="取消研究" title="取消研究">
              <Square size={15} fill="currentColor" aria-hidden="true" />
            </button>
          ) : busy ? (
            <button type="button" className={styles.pendingButton} disabled aria-label="正在启动研究" title="正在启动研究">
              <LoaderCircle className={styles.spinning} size={18} aria-hidden="true" />
            </button>
          ) : (
            <button
              type="button"
              className={styles.sendButton}
              onClick={submit}
              disabled={uploading || Boolean(disabledReason)}
              aria-label="开始研究"
              title={disabledReason ?? "开始研究"}
            >
              <Send size={18} aria-hidden="true" />
            </button>
          )}
        </div>
      </div>
      <div className={styles.metaRow}>
        <span id={`${errorId}-message`} className={styles.error} role="status" aria-live="polite">
          {error ?? disabledReason ?? ""}
        </span>
        <span className={styles.count}>{queryCharacterCount(value)}/{QUERY_MAX_LENGTH}</span>
      </div>
    </div>
  );
}
