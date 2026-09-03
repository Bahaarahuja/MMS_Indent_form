function apiUrl(path) {
  return path.startsWith('/') ? path : `/${path}`;
}

function fileUrl(path) {
  if (!path) return '';
  if (/^https?:\/\//i.test(path)) return path;
  return apiUrl(path);
}
