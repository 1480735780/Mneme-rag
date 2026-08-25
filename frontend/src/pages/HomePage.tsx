// M0 #5 受保护首页占位：/ 重定向到 /chat，此页极少直达
export default function HomePage() {
  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-center">
      <h1 className="text-2xl font-semibold">mneme-rag</h1>
      <p className="text-muted-foreground">正在跳转到对话页…</p>
    </div>
  );
}
