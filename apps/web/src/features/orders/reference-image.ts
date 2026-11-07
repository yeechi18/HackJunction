import { ApiError, errorMessage } from "@/lib/api";

export function formatBytes(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(2)} MiB` : `${Math.max(1, Math.ceil(bytes / 1024))} KiB`;
}

export function validateFile(file: File): string | null {
  if (!file.size) return "这张图片是空文件，请重新选择。";
  if (file.size > 10 * 1024 * 1024) return "单张图片不能超过 10 MiB，请缩小图片后再上传。";
  const knownType = ["image/png", "image/jpeg", "image/webp"].includes(file.type);
  const unknownTypeWithImageExtension = !file.type && /\.(png|jpe?g|webp)$/i.test(file.name);
  return knownType || unknownTypeWithImageExtension ? null : "请选择 PNG、JPEG 或 WebP 格式的图片。";
}

export function uploadErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 413) return "上传图片超过大小限制，请选择不超过 10 MiB 的图片。";
    if (error.status === 415) return "图片格式不支持，请选择 PNG、JPEG 或 WebP 图片。";
    if (error.status === 422) return "图片未通过校验，请检查图片是否完整，并重新选择后上传。";
  }
  return errorMessage(error);
}
