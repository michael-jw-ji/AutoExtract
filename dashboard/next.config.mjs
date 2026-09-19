/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {
    // 127.0.0.1, NOT localhost. uvicorn binds IPv4-only, while Windows
    // resolves `localhost` to ::1 first -- that connection stalls ~2s before
    // falling back to IPv4. Measured: localhost 2061ms vs 127.0.0.1 3.8ms on
    // the same endpoint. The dashboard polls every 5s, so it matters.
    NEXT_PUBLIC_API: process.env.NEXT_PUBLIC_API ?? "http://127.0.0.1:8000",
  },
};

export default nextConfig;
