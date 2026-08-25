// M4C T8 摄取页：流水线 / 任务 双 Tab 容器
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import PipelinesTab from "./PipelinesTab";
import TasksTab from "./TasksTab";

export default function IngestionPage() {
  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div>
        <h1 className="text-lg font-semibold">摄取流水线</h1>
        <p className="text-sm text-muted-foreground">编排处理节点，上传文档驱动任务执行并查看状态</p>
      </div>
      <Tabs defaultValue="pipelines">
        <TabsList>
          <TabsTrigger value="pipelines">流水线</TabsTrigger>
          <TabsTrigger value="tasks">任务</TabsTrigger>
        </TabsList>
        <TabsContent value="pipelines">
          <PipelinesTab />
        </TabsContent>
        <TabsContent value="tasks">
          <TasksTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
