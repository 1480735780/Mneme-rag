// M0 #5 404 页
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";

export default function NotFoundPage() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-4">
      <h1 className="text-4xl font-bold">404</h1>
      <p className="text-muted-foreground">页面不存在</p>
      <Button variant="outline" render={<Link to="/" />}>返回首页</Button>
    </main>
  );
}
