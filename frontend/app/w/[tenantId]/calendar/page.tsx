import { CalendarView } from "@/components/CalendarView";
import { WorkspaceTabs } from "@/components/WorkspaceTabs";

export default async function CalendarPage({ params }: { params: Promise<{ tenantId: string }> }) {
  const { tenantId } = await params;
  return (
    <>
      <WorkspaceTabs tenantId={tenantId} />
      <CalendarView tenantId={tenantId} />
    </>
  );
}
