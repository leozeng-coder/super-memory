import type { Metadata } from "next";
import { MemoryWorkspace } from "./MemoryWorkspace";

export const metadata: Metadata = {
  title: "Super Memory",
  description: "查看、筛选、关联和编辑经过审核的本地开发记忆。",
};

export default function Home() {
  return <MemoryWorkspace />;
}
