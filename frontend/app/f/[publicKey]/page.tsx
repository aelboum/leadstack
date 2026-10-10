import { PublicForm } from "@/components/PublicForm";

export default async function PublicFormPage({ params }: { params: Promise<{ publicKey: string }> }) {
  const { publicKey } = await params;
  return <PublicForm publicKey={publicKey} />;
}
