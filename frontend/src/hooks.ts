import { useEffect, useState } from "react";
import { message, request } from "./api";

export function useResource<T>(
  url: string | null,
  decode: (value: unknown) => T,
  revision = 0,
) {
  const [state, setState] = useState<{
    key: string | null;
    revision: number;
    data: T | null;
    error: string | null;
    loading: boolean;
  }>({ key: null, revision, data: null, error: null, loading: false });
  useEffect(() => {
    if (!url) return;
    const controller = new AbortController();
    let active = true;
    request(url, decode, { signal: controller.signal })
      .then((data) => {
        if (active)
          setState({ key: url, revision, data, error: null, loading: false });
      })
      .catch((error) => {
        if (active)
          setState({
            key: url,
            revision,
            data: null,
            error: message(error),
            loading: false,
          });
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [url, decode, revision]);
  if (state.key !== url || state.revision !== revision)
    return { data: null, error: null, loading: Boolean(url) };
  return state;
}
