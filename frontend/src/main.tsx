import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { ApiError } from "./api/client";
import App from "./App";
import { SizingProvider } from "./components/SizingProvider";
import { ThemeProvider } from "./components/ThemeProvider";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      // 4xx and "no data yet" won't fix themselves; retry only transient failures.
      retry: (failures, error) =>
        failures < 2 && !(error instanceof ApiError && (error.unreachable || error.status === 503 || (error.status >= 400 && error.status < 500))),
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <SizingProvider>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </SizingProvider>
    </ThemeProvider>
  </StrictMode>,
);
