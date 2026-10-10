import { ConsentAction } from "@/components/ConsentAction";

export default async function ConfirmPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <ConsentAction token={token} action="confirm" />;
}
