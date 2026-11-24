import React from 'react';
import { Sparkles } from 'lucide-react';
import { BriefingWidget } from '../components/features/BriefingWidget';

const MorningBriefing: React.FC = () => {
  return (
    <div className="max-w-4xl mx-auto space-y-8 animate-in fade-in zoom-in duration-500 pb-20">
      <div className="text-center space-y-2">
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-primary/10 text-primary mb-4">
          <Sparkles size={32} />
        </div>
        <h1 className="text-4xl font-black tracking-tight">Morning Briefing</h1>
        <p className="text-gray-500 dark:text-gray-400">Your AI-powered daily overview in a dedicated page view.</p>
      </div>

      <BriefingWidget variant="full" className="shadow-xl shadow-primary/5" />
    </div>
  );
};

export default MorningBriefing;
